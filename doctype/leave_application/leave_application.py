# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
from re import template
import frappe
from frappe import _
from frappe.defaults import clear_user_default
from frappe.monitor import start
from frappe.utils import cint, cstr, date_diff, flt, formatdate, getdate, get_link_to_form, \
	comma_or, get_fullname, add_days, nowdate, get_datetime_str
from erpnext.hr.utils import set_employee_name, get_leave_period, share_doc_with_approver
from erpnext.hr.doctype.leave_block_list.leave_block_list import get_applicable_block_dates
from erpnext.hr.doctype.employee.employee import get_holiday_list_for_employee
from erpnext.buying.doctype.supplier_scorecard.supplier_scorecard import daterange
from erpnext.hr.doctype.leave_ledger_entry.leave_ledger_entry import create_leave_ledger_entry
from datetime import timedelta, date

from frappe.utils.data import new_line_sep
class LeaveDayBlockedError(frappe.ValidationError): pass
class OverlapError(frappe.ValidationError): pass
class AttendanceAlreadyMarkedError(frappe.ValidationError): pass
class NotAnOptionalHoliday(frappe.ValidationError): pass

from frappe.model.document import Document
class LeaveApplication(Document):
	def get_feed(self):
		return _("{0}: From {0} of type {1}").format(self.employee_name, self.leave_type)

	def validate(self):
		set_employee_name(self)
		self.validate_dates()
		###custom code for sending email
		self.validate_notification()
		self.validate_balance_leaves()
		self.validate_leave_overlap()
		self.validate_max_days()
		self.show_block_day_warning()
		self.validate_block_days()
		self.validate_salary_processed_days()
		self.validate_attendance()
		self.set_half_day_date()
		self.set_quarter_day_date()
		validate_restrict_backdated_leave_application(self)
		validate_maximum_continuous_days(self)
		validate_notice_period(self)
		if frappe.db.get_value("Leave Type", self.leave_type, 'is_optional_leave'):
			self.validate_optional_leave()
		self.validate_applicable_after()
		

	def on_update(self):
		if self.status == "Open" and self.docstatus < 1:
			# notify leave approver about creation
			if frappe.db.get_single_value("HR Settings", "send_leave_notification"):
				self.notify_leave_approver()

		share_doc_with_approver(self, self.leave_approver)

	def on_submit(self):
		if self.status == "Open":
			frappe.throw(_("Only Leave Applications with status 'Approved' and 'Rejected' can be submitted"))

		self.validate_back_dated_application()
		self.update_attendance()

		# notify leave applier about approval
		if frappe.db.get_single_value("HR Settings", "send_leave_notification"):
			self.notify_employee()
		self.create_leave_ledger_entry()
		self.reload()

	def before_cancel(self):
		self.status = "Cancelled"

	def on_cancel(self):
		self.create_leave_ledger_entry(submit=False)
		# notify leave applier about cancellation
		if frappe.db.get_single_value("HR Settings", "send_leave_notification"):
			self.notify_employee()
		self.cancel_attendance()

	def validate_applicable_after(self):
		if self.leave_type:
			leave_type = frappe.get_doc("Leave Type", self.leave_type)
			if leave_type.applicable_after > 0:
				date_of_joining = frappe.db.get_value("Employee", self.employee, "date_of_joining")
				leave_days = get_approved_leaves_for_period(self.employee, False, date_of_joining, self.from_date)
				number_of_days = date_diff(getdate(self.from_date), date_of_joining)
				if number_of_days >= 0:
					holidays = 0
					if not frappe.db.get_value("Leave Type", self.leave_type, "include_holiday"):
						holidays = get_holidays(self.employee, date_of_joining, self.from_date)
					number_of_days = number_of_days - leave_days - holidays
					if number_of_days < leave_type.applicable_after:
						frappe.throw(_("{0} applicable after {1} working days").format(self.leave_type, leave_type.applicable_after))

	def validate_dates(self):
		if frappe.db.get_single_value("HR Settings", "restrict_backdated_leave_application"):
			if self.from_date and self.from_date < frappe.utils.today():
				allowed_role = frappe.db.get_single_value("HR Settings", "role_allowed_to_create_backdated_leave_application")
				if allowed_role not in frappe.get_roles():
					frappe.throw(_("Only users with the {0} role can create backdated leave applications").format(allowed_role))

		if self.from_date and self.to_date and (getdate(self.to_date) < getdate(self.from_date)):
			frappe.throw(_("To date cannot be before from date"))

		if self.half_day and self.half_day_date \
			and (getdate(self.half_day_date) < getdate(self.from_date)
			or getdate(self.half_day_date) > getdate(self.to_date)):

				frappe.throw(_("Half Day Date should be between From Date and To Date"))

		if self.quarter_day and self.quarter_day_date \
			and (getdate(self.quarter_day_date) < getdate(self.from_date)
			or getdate(self.quarter_day_date) > getdate(self.to_date)):

				frappe.throw(_("Quarter Day Date should be between From Date and To Date"))

		if not is_lwp(self.leave_type):
			self.validate_dates_across_allocation()
			self.validate_back_dated_application()
		
	
	###custom code for mail notification to approve the leave application record
	def validate_notification(self):
		
		subj = 'Notification – manager approval of leave ' + self.employee_name
		notification_message = 'Hi '+ '\n' + self.employee_name +' has submitted a leave application for your approval.' + '\n' + 'Thanks' + '\n' + 'HR'
		
		frappe.sendmail(self.leave_approver,subject=subj,\
				message = notification_message)

	def validate_dates_across_allocation(self):
		if frappe.db.get_value("Leave Type", self.leave_type, "allow_negative"):
			return
		def _get_leave_allocation_record(date):
			allocation = frappe.db.sql("""select name from `tabLeave Allocation`
				where employee=%s and leave_type=%s and docstatus=1
				and %s between from_date and to_date""", (self.employee, self.leave_type, date))

			return allocation and allocation[0][0]

		allocation_based_on_from_date = _get_leave_allocation_record(self.from_date)
		allocation_based_on_to_date = _get_leave_allocation_record(self.to_date)

		if not (allocation_based_on_from_date or allocation_based_on_to_date):
			frappe.throw(_("Application period cannot be outside leave allocation period"))

		elif allocation_based_on_from_date != allocation_based_on_to_date:
			frappe.throw(_("Application period cannot be across two allocation records"))

	def validate_back_dated_application(self):
		future_allocation = frappe.db.sql("""select name, from_date from `tabLeave Allocation`
			where employee=%s and leave_type=%s and docstatus=1 and from_date > %s
			and carry_forward=1""", (self.employee, self.leave_type, self.to_date), as_dict=1)

		if future_allocation:
			frappe.throw(_("Leave cannot be applied/cancelled before {0}, as leave balance has already been carry-forwarded in the future leave allocation record {1}")
				.format(formatdate(future_allocation[0].from_date), future_allocation[0].name))

	def update_attendance(self):
		if self.status == "Approved":
			for dt in daterange(getdate(self.from_date), getdate(self.to_date)):
				date = dt.strftime("%Y-%m-%d")
				status = "Half Day" if self.half_day_date and getdate(date) == getdate(self.half_day_date) else "On Leave"
				status = "Quarter Day" if self.quarter_day_date and getdate(date) == getdate(self.quarter_day_date) else "On Leave"
				attendance_name = frappe.db.exists('Attendance', dict(employee = self.employee,
					attendance_date = date, docstatus = ('!=', 2)))

				if attendance_name:
					# update existing attendance, change absent to on leave
					doc = frappe.get_doc('Attendance', attendance_name)
					if doc.status != status:
						doc.db_set('status', status)
						doc.db_set('leave_type', self.leave_type)
						doc.db_set('leave_application', self.name)
				else:
					# make new attendance and submit it
					doc = frappe.new_doc("Attendance")
					doc.employee = self.employee
					doc.employee_name = self.employee_name
					doc.attendance_date = date
					doc.company = self.company
					doc.leave_type = self.leave_type
					doc.leave_application = self.name
					doc.status = status
					doc.flags.ignore_validate = True
					doc.insert(ignore_permissions=True)
					doc.submit()

	def cancel_attendance(self):
		if self.docstatus == 2:
			attendance = frappe.db.sql("""select name from `tabAttendance` where employee = %s\
				and (attendance_date between %s and %s) and docstatus < 2 and status in ('On Leave', 'Half Day')""",(self.employee, self.from_date, self.to_date), as_dict=1)
			for name in attendance:
				frappe.db.set_value("Attendance", name, "docstatus", 2)

	def validate_salary_processed_days(self):
		if not frappe.db.get_value("Leave Type", self.leave_type, "is_lwp"):
			return

		last_processed_pay_slip = frappe.db.sql("""
			select start_date, end_date from `tabSalary Slip`
			where docstatus = 1 and employee = %s
			and ((%s between start_date and end_date) or (%s between start_date and end_date))
			order by modified desc limit 1
		""",(self.employee, self.to_date, self.from_date))

		if last_processed_pay_slip:
			frappe.throw(_("Salary already processed for period between {0} and {1}, Leave application period cannot be between this date range.").format(formatdate(last_processed_pay_slip[0][0]),
				formatdate(last_processed_pay_slip[0][1])))


	def show_block_day_warning(self):
		block_dates = get_applicable_block_dates(self.from_date, self.to_date,
			self.employee, self.company, all_lists=True)

		if block_dates:
			frappe.msgprint(_("Warning: Leave application contains following block dates") + ":")
			for d in block_dates:
				frappe.msgprint(formatdate(d.block_date) + ": " + d.reason)

	def validate_block_days(self):
		block_dates = get_applicable_block_dates(self.from_date, self.to_date,
			self.employee, self.company)

		if block_dates and self.status == "Approved":
			frappe.throw(_("You are not authorized to approve leaves on Block Dates"), LeaveDayBlockedError)

	def validate_balance_leaves(self):
		if self.from_date and self.to_date and self.quarter_day:
			frappe.msgprint("if called")
			self.total_leave_days = get_no_of_leave_days(self.employee, self.leave_type,
				self.from_date, self.to_date, self.quarter_day, self.quarter_day_date)

		if self.from_date and self.to_date and self.half_day:
			self.total_leave_days = get_number_of_leave_days(self.employee, self.leave_type,
				self.from_date, self.to_date, self.half_day, self.half_day_date)

			

			if self.total_leave_days <= 0:
				frappe.throw(_("The day(s) on which you are applying for leave are holidays. You need not apply for leave."))

			if not is_lwp(self.leave_type):
				self.leave_balance = get_leave_balance_on(self.employee, self.leave_type, self.from_date, self.to_date,
					consider_all_leaves_in_the_allocation_period=True)
				if self.status != "Rejected" and (self.leave_balance < self.total_leave_days or not self.leave_balance):
					if frappe.db.get_value("Leave Type", self.leave_type, "allow_negative"):
						frappe.msgprint(_("Note: There is not enough leave balance for Leave Type {0}")
							.format(self.leave_type))
					else:
						frappe.throw(_("There is not enough leave balance for Leave Type {0}")
							.format(self.leave_type))

	def validate_leave_overlap(self):
		if not self.name:
			# hack! if name is null, it could cause problems with !=
			self.name = "New Leave Application"

		for d in frappe.db.sql("""
			select
				name, leave_type, posting_date, from_date, to_date, total_leave_days, half_day_date
			from `tabLeave Application`
			where employee = %(employee)s and docstatus < 2 and status in ("Open", "Approved")
			and to_date >= %(from_date)s and from_date <= %(to_date)s
			and name != %(name)s""", {
				"employee": self.employee,
				"from_date": self.from_date,
				"to_date": self.to_date,
				"name": self.name
			}, as_dict = 1):

			if cint(self.half_day)==1 and getdate(self.half_day_date) == getdate(d.half_day_date) and (
				flt(self.total_leave_days)==0.5
				or getdate(self.from_date) == getdate(d.to_date)
				or getdate(self.to_date) == getdate(d.from_date)):

				total_leaves_on_half_day = self.get_total_leaves_on_half_day()
				if total_leaves_on_half_day >= 1:
					self.throw_overlap_error(d)

			if cint(self.quarter_day)==1 and getdate(self.quarter_day_date) == getdate(d.quarter_day_date) and (
				flt(self.total_leave_days)==0.25
				or getdate(self.from_date) == getdate(d.to_date)
				or getdate(self.to_date) == getdate(d.from_date)):

				total_leaves_on_quarter_day = self.get_total_leaves_on_quarter_day()
				if total_leaves_on_quarter_day >= 1:
					self.throw_overlap_error(d)

			else:
				self.throw_overlap_error(d)

	
	def throw_overlap_error(self, d):
		msg = _("Employee {0} has already applied for {1} between {2} and {3} : ").format(self.employee,
			d['leave_type'], formatdate(d['from_date']), formatdate(d['to_date'])) \
			+ """ <b><a href="/app/Form/Leave Application/{0}">{0}</a></b>""".format(d["name"])
		frappe.throw(msg, OverlapError)

	def get_total_leaves_on_half_day(self):
		leave_count_on_half_day_date = frappe.db.sql("""select count(name) from `tabLeave Application`
			where employee = %(employee)s
			and docstatus < 2
			and status in ("Open", "Approved")
			and half_day = 1
			and half_day_date = %(half_day_date)s
			and name != %(name)s""", {
				"employee": self.employee,
				"half_day_date": self.half_day_date,
				"name": self.name
			})[0][0]

		return leave_count_on_half_day_date * 0.5

	def get_total_leaves_on_quarter_day(self):
		leave_count_on_quarter_day_date = frappe.db.sql("""select count(name) from `tabLeave Application`
			where employee = %(employee)s
			and docstatus < 2
			and status in ("Open", "Approved")
			and quarter_day = 1
			and quarter_day_date = %(quarter_day_date)s
			and name != %(name)s""", {
				"employee": self.employee,
				"quarter_day_date": self.quarter_day_date,
				"name": self.name
			})[0][0]

		return leave_count_on_quarter_day_date * 0.25

	def validate_max_days(self):
		max_days = frappe.db.get_value("Leave Type", self.leave_type, "max_continuous_days_allowed")
		if max_days and self.total_leave_days > cint(max_days):
			frappe.throw(_("Leave of type {0} cannot be longer than {1}").format(self.leave_type, max_days))

	def validate_attendance(self):
		attendance = frappe.db.sql("""select name from `tabAttendance` where employee = %s and (attendance_date between %s and %s)
					and status = "Present" and docstatus = 1""",
			(self.employee, self.from_date, self.to_date))
		if attendance:
			frappe.throw(_("Attendance for employee {0} is already marked for this day").format(self.employee),
				AttendanceAlreadyMarkedError)

	def validate_optional_leave(self):
		leave_period = get_leave_period(self.from_date, self.to_date, self.company)
		if not leave_period:
			frappe.throw(_("Cannot find active Leave Period"))
		optional_holiday_list = frappe.db.get_value("Leave Period", leave_period[0]["name"], "optional_holiday_list")
		if not optional_holiday_list:
			frappe.throw(_("Optional Holiday List not set for leave period {0}").format(leave_period[0]["name"]))
		day = getdate(self.from_date)
		while day <= getdate(self.to_date):
			if not frappe.db.exists({"doctype": "Holiday", "parent": optional_holiday_list, "holiday_date": day}):
				frappe.throw(_("{0} is not in Optional Holiday List").format(formatdate(day)), NotAnOptionalHoliday)
			day = add_days(day, 1)

	def set_half_day_date(self):
		if self.from_date == self.to_date and self.half_day == 1:
			self.half_day_date = self.from_date

		if self.half_day == 0:
			self.half_day_date = None

	def set_quarter_day_date(self):
		if self.from_date == self.to_date and self.quarter_day == 1:
			self.quarter_day_date = self.from_date

		if self.quarter_day == 0:
			self.quarter_day_date = None

	def notify_employee(self):
		employee = frappe.get_doc("Employee", self.employee)
		if not employee.user_id:
			return

		parent_doc = frappe.get_doc('Leave Application', self.name)
		args = parent_doc.as_dict()

		template = frappe.db.get_single_value('HR Settings', 'leave_status_notification_template')
		if not template:
			frappe.msgprint(_("Please set default template for Leave Status Notification in HR Settings."))
			return
		email_template = frappe.get_doc("Email Template", template)
		message = frappe.render_template(email_template.response, args)

		self.notify({
			# for post in messages
			"message": message,
			"message_to": employee.user_id,
			# for email
			"subject": email_template.subject,
			"notify": "employee"
		})

	def notify_leave_approver(self):
		if self.leave_approver:
			parent_doc = frappe.get_doc('Leave Application', self.name)
			args = parent_doc.as_dict()

			template = frappe.db.get_single_value('HR Settings', 'leave_approval_notification_template')
			if not template:
				frappe.msgprint(_("Please set default template for Leave Approval Notification in HR Settings."))
				return
			email_template = frappe.get_doc("Email Template", template)
			message = frappe.render_template(email_template.response, args)

			self.notify({
				# for post in messages
				"message": message,
				"message_to": self.leave_approver,
				# for email
				"subject": email_template.subject
			})

	def notify(self, args):
		args = frappe._dict(args)
		# args -> message, message_to, subject
		if cint(self.follow_via_email):
			contact = args.message_to
			if not isinstance(contact, list):
				if not args.notify == "employee":
					contact = frappe.get_doc('User', contact).email or contact

			sender      	    = dict()
			sender['email']     = frappe.get_doc('User', frappe.session.user).email
			sender['full_name'] = frappe.utils.get_fullname(sender['email'])

			try:
				frappe.sendmail(
					recipients = contact,
					sender = sender['email'],
					subject = args.subject,
					message = args.message,
				)
				frappe.msgprint(_("Email sent to {0}").format(contact))
			except frappe.OutgoingEmailError:
				pass

	def create_leave_ledger_entry(self, submit=True):
		if self.status != 'Approved' and submit:
			return

		expiry_date = get_allocation_expiry(self.employee, self.leave_type,
			self.to_date, self.from_date)

		lwp = frappe.db.get_value("Leave Type", self.leave_type, "is_lwp")

		if expiry_date:
			self.create_ledger_entry_for_intermediate_allocation_expiry(expiry_date, submit, lwp)
		else:
			raise_exception = True
			if frappe.flags.in_patch:
				raise_exception=False

			args = dict(
				leaves=self.total_leave_days * -1,
				from_date=self.from_date,
				to_date=self.to_date,
				is_lwp=lwp,
				holiday_list=get_holiday_list_for_employee(self.employee, raise_exception=raise_exception) or ''
			)
			create_leave_ledger_entry(self, args, submit)

	def create_ledger_entry_for_intermediate_allocation_expiry(self, expiry_date, submit, lwp):
		''' splits leave application into two ledger entries to consider expiry of allocation '''

		raise_exception = True
		if frappe.flags.in_patch:
			raise_exception=False

		args = dict(
			from_date=self.from_date,
			to_date=expiry_date,
			leaves=(date_diff(expiry_date, self.from_date) + 1) * -1,
			is_lwp=lwp,
			holiday_list=get_holiday_list_for_employee(self.employee, raise_exception=raise_exception) or ''
		)
		create_leave_ledger_entry(self, args, submit)

		if getdate(expiry_date) != getdate(self.to_date):
			start_date = add_days(expiry_date, 1)
			args.update(dict(
				from_date=start_date,
				to_date=self.to_date,
				leaves=date_diff(self.to_date, expiry_date) * -1
			))
			create_leave_ledger_entry(self, args, submit)


def get_allocation_expiry(employee, leave_type, to_date, from_date):
	''' Returns expiry of carry forward allocation in leave ledger entry '''
	expiry =  frappe.get_all("Leave Ledger Entry",
		filters={
			'employee': employee,
			'leave_type': leave_type,
			'is_carry_forward': 1,
			'transaction_type': 'Leave Allocation',
			'to_date': ['between', (from_date, to_date)]
		},fields=['to_date'])
	return expiry[0]['to_date'] if expiry else None

@frappe.whitelist()
def get_number_of_leave_days(employee, leave_type, from_date, to_date, half_day = None, half_day_date = None, holiday_list = None):
	number_of_days = 0
	if cint(half_day) == 1:
		if from_date == to_date:
			number_of_days = 0.5
		elif half_day_date and half_day_date <= to_date:
			number_of_days = date_diff(to_date, from_date) + .5
		else:
			number_of_days = date_diff(to_date, from_date) + 1

	else:
		number_of_days = date_diff(to_date, from_date) + 1

	if not frappe.db.get_value("Leave Type", leave_type, "include_holiday"):
		number_of_days = flt(number_of_days) - flt(get_holidays(employee, from_date, to_date, holiday_list=holiday_list))
	return number_of_days

###Custom Code for no of leave days for quarter day
@frappe.whitelist()
def get_no_of_leave_days(employee, leave_type, from_date, to_date, quarter_day = None, quarter_day_date = None, holiday_list = None):
	no_of_days = 0
	if cint(quarter_day) == 1:
		if from_date == to_date:
			no_of_days = 0.25
		elif quarter_day_date and quarter_day_date <= to_date:
			no_of_days = date_diff(to_date, from_date) + .5
		else:
			no_of_days = date_diff(to_date, from_date) + 1

	else:
		no_of_days = date_diff(to_date, from_date) + 1

	if not frappe.db.get_value("Leave Type", leave_type, "include_holiday"):
		no_of_days = flt(no_of_days) - flt(get_holidays(employee, from_date, to_date, holiday_list=holiday_list))
	return no_of_days

@frappe.whitelist()
def get_leave_details(employee, date):
	allocation_records = get_leave_allocation_records(employee, date)
	leave_allocation = {}
	for d in allocation_records:
		allocation = allocation_records.get(d, frappe._dict())

		total_allocated_leaves = frappe.db.get_value('Leave Allocation', {
			'from_date': ('<=', date),
			'to_date': ('>=', date),
			'employee': employee,
			'leave_type': allocation.leave_type,
		}, 'SUM(total_leaves_allocated)') or 0

		remaining_leaves = get_leave_balance_on(employee, d, date, to_date = allocation.to_date,
			consider_all_leaves_in_the_allocation_period=True)

		end_date = allocation.to_date
		leaves_taken = get_leaves_for_period(employee, d, allocation.from_date, end_date) * -1
		leaves_pending = get_pending_leaves_for_period(employee, d, allocation.from_date, end_date)
		#frappe.msgprint("leave"+str(d) +"from" +str(allocation.from_date))
	#	leave_additional = get_pending_leaves_for_period(employee, d, allocation.from_date, end_date) * +1
		
		leave_allocation[d] = {
			"total_leaves": total_allocated_leaves,
			"expired_leaves": total_allocated_leaves - (remaining_leaves + leaves_taken),
			"leaves_taken": leaves_taken,
			"pending_leaves": leaves_pending,
#			"additional_leave": leave_additional,
			"remaining_leaves": remaining_leaves,
			}

	#is used in set query
	lwps = frappe.get_list("Leave Type", filters = {"is_lwp": 1})
	lwps = [lwp.name for lwp in lwps]

	ret = {
		'leave_allocation': leave_allocation,
		'leave_approver': get_leave_approver(employee),
		'lwps': lwps
	}

	return ret

@frappe.whitelist()
def get_leave_balance_on(employee, leave_type, date, to_date=None, consider_all_leaves_in_the_allocation_period=False):
	'''
		Returns leave balance till date
		:param employee: employee name
		:param leave_type: leave type
		:param date: date to check balance on
		:param to_date: future date to check for allocation expiry
		:param consider_all_leaves_in_the_allocation_period: consider all leaves taken till the allocation end date
	'''

	if not to_date:
		to_date = nowdate()

	allocation_records = get_leave_allocation_records(employee, date, leave_type)
	allocation = allocation_records.get(leave_type, frappe._dict())

	end_date = allocation.to_date if consider_all_leaves_in_the_allocation_period else date
	expiry = get_allocation_expiry(employee, leave_type, to_date, date)

	leaves_taken = get_leaves_for_period(employee, leave_type, allocation.from_date, end_date)

	return get_remaining_leaves(allocation, leaves_taken, date, expiry)

def get_leave_allocation_records(employee, date, leave_type=None):
	''' returns the total allocated leaves and carry forwarded leaves based on ledger entries '''

	conditions = ("and leave_type='%s'" % leave_type) if leave_type else ""
	allocation_details = frappe.db.sql("""
		SELECT
			SUM(CASE WHEN is_carry_forward = 1 THEN leaves ELSE 0 END) as cf_leaves,
			SUM(CASE WHEN is_carry_forward = 0 THEN leaves ELSE 0 END) as new_leaves,
			MIN(from_date) as from_date,
			MAX(to_date) as to_date,
			leave_type
		FROM `tabLeave Ledger Entry`
		WHERE
			from_date <= %(date)s
			AND to_date >= %(date)s
			AND docstatus=1
			AND transaction_type="Leave Allocation"
			AND employee=%(employee)s
			AND is_expired=0
			AND is_lwp=0
			{0}
		GROUP BY employee, leave_type
	""".format(conditions), dict(date=date, employee=employee), as_dict=1) #nosec

	allocated_leaves = frappe._dict()
	for d in allocation_details:
		allocated_leaves.setdefault(d.leave_type, frappe._dict({
			"from_date": d.from_date,
			"to_date": d.to_date,
			"total_leaves_allocated": flt(d.cf_leaves) + flt(d.new_leaves),
			"unused_leaves": d.cf_leaves,
			"new_leaves_allocated": d.new_leaves,
			"leave_type": d.leave_type,
			
		}))
	return allocated_leaves

def get_pending_leaves_for_period(employee, leave_type, from_date, to_date):
	''' Returns leaves that are pending approval '''
	leaves = frappe.get_all("Leave Application",
		filters={
			"employee": employee,
			"leave_type": leave_type,
			"status": "Open"
		},
		or_filters={
			"from_date": ["between", (from_date, to_date)],
			"to_date": ["between", (from_date, to_date)]
		}, fields=['SUM(total_leave_days) as leaves'])[0]
	return leaves['leaves'] if leaves['leaves'] else 0.0

def get_additional_leave(employee, leave_type, from_date, to_date):
	''' Returns leaves that are Additional Leave '''
	leaves = frappe.get_all("Leave Application",
		filters={
			"employee": employee,
			"leave_type": leave_type,
			"status": "Open"
		},
		or_filters={
			"from_date": ["between", (from_date, to_date)],
			"to_date": ["between", (from_date, to_date)]
		}, fields=['SUM(additional_leave) as leaves'])[0]
	return leaves['leaves'] if leaves['leaves'] else 0.0

	

def get_remaining_leaves(allocation, leaves_taken, date, expiry):
	''' Returns minimum leaves remaining after comparing with remaining days for allocation expiry '''
	def _get_remaining_leaves(remaining_leaves, end_date):

		if remaining_leaves > 0:
			remaining_days = date_diff(end_date, date) + 1
			remaining_leaves = min(remaining_days, remaining_leaves)

		return remaining_leaves

	total_leaves = flt(allocation.total_leaves_allocated) + flt(leaves_taken)

	if expiry and allocation.unused_leaves:
		remaining_leaves = flt(allocation.unused_leaves) + flt(leaves_taken)
		remaining_leaves = _get_remaining_leaves(remaining_leaves, expiry)

		total_leaves = flt(allocation.new_leaves_allocated) + flt(remaining_leaves)

	return _get_remaining_leaves(total_leaves, allocation.to_date)

def get_leaves_for_period(employee, leave_type, from_date, to_date, do_not_skip_expired_leaves=False):
	leave_entries = get_leave_entries(employee, leave_type, from_date, to_date)
	leave_days = 0

	for leave_entry in leave_entries:
		inclusive_period = leave_entry.from_date >= getdate(from_date) and leave_entry.to_date <= getdate(to_date)

		if  inclusive_period and leave_entry.transaction_type == 'Leave Encashment':
			leave_days += leave_entry.leaves

		elif inclusive_period and leave_entry.transaction_type == 'Leave Allocation' and leave_entry.is_expired \
			and (do_not_skip_expired_leaves or not skip_expiry_leaves(leave_entry, to_date)):
			leave_days += leave_entry.leaves

		elif leave_entry.transaction_type == 'Leave Application':
			if leave_entry.from_date < getdate(from_date):
				leave_entry.from_date = from_date
			if leave_entry.to_date > getdate(to_date):
				leave_entry.to_date = to_date

			half_day = 0
			half_day_date = None
			# fetch half day date for leaves with half days
			if leave_entry.leaves % 1:
				half_day = 1
				half_day_date = frappe.db.get_value('Leave Application',
					{'name': leave_entry.transaction_name}, ['half_day_date'])

			quarter_day = 0
			quarter_day_date = None
			# fetch quarter day date for leaves with quarter days
			if leave_entry.leaves % 1:
				quarter_day = 1
				quarter_day_date = frappe.db.get_value('Leave Application',
					{'name': leave_entry.transaction_name}, ['quarter_day_date'])

			leave_days += get_no_of_leave_days(employee, leave_type,
				leave_entry.from_date, leave_entry.to_date, quarter_day, quarter_day_date, holiday_list=leave_entry.holiday_list) * -1

	return leave_days

def skip_expiry_leaves(leave_entry, date):
	''' Checks whether the expired leaves coincide with the to_date of leave balance check.
		This allows backdated leave entry creation for non carry forwarded allocation '''
	end_date = frappe.db.get_value("Leave Allocation", {'name': leave_entry.transaction_name}, ['to_date'])
	return True if end_date == date and not leave_entry.is_carry_forward else False

def get_leave_entries(employee, leave_type, from_date, to_date):
	''' Returns leave entries between from_date and to_date. '''
	return frappe.db.sql("""
		SELECT
			employee, leave_type, from_date, to_date, leaves, transaction_name, transaction_type, holiday_list,
			is_carry_forward, is_expired
		FROM `tabLeave Ledger Entry`
		WHERE employee=%(employee)s AND leave_type=%(leave_type)s
			AND docstatus=1
			AND (leaves<0
				OR is_expired=1)
			AND (from_date between %(from_date)s AND %(to_date)s
				OR to_date between %(from_date)s AND %(to_date)s
				OR (from_date < %(from_date)s AND to_date > %(to_date)s))
	""", {
		"from_date": from_date,
		"to_date": to_date,
		"employee": employee,
		"leave_type": leave_type
	}, as_dict=1)

@frappe.whitelist()
def get_holidays(employee, from_date, to_date, holiday_list = None):
	'''get holidays between two dates for the given employee'''
	if not holiday_list:
		holiday_list = get_holiday_list_for_employee(employee)

	holidays = frappe.db.sql("""select count(distinct holiday_date) from `tabHoliday` h1, `tabHoliday List` h2
		where h1.parent = h2.name and h1.holiday_date between %s and %s
		and h2.name = %s""", (from_date, to_date, holiday_list))[0][0]

	return holidays

def is_lwp(leave_type):
	lwp = frappe.db.sql("select is_lwp from `tabLeave Type` where name = %s", leave_type)
	return lwp and cint(lwp[0][0]) or 0

@frappe.whitelist()
def get_events(start, end, filters=None):
	events = []

	employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, ["name", "company"],
		as_dict=True)
	if employee:
		employee, company = employee.name, employee.company
	else:
		employee=''
		company=frappe.db.get_value("Global Defaults", None, "default_company")

	from frappe.desk.reportview import get_filters_cond
	conditions = get_filters_cond("Leave Application", filters, [])
	# show department leaves for employee
	if "Employee" in frappe.get_roles():
		add_department_leaves(events, start, end, employee, company)

	add_leaves(events, start, end, conditions)

	add_block_dates(events, start, end, employee, company)
	add_holidays(events, start, end, employee, company)
	return events

def add_department_leaves(events, start, end, employee, company):
	department = frappe.db.get_value("Employee", employee, "department")

	if not department:
		return

	# department leaves
	department_employees = frappe.db.sql_list("""select name from tabEmployee where department=%s
		and company=%s""", (department, company))

	filter_conditions = " and employee in (\"%s\")" % '", "'.join(department_employees)
	add_leaves(events, start, end, filter_conditions=filter_conditions)

def add_leaves(events, start, end, filter_conditions=None):
	conditions = []


	if not cint(frappe.db.get_value("HR Settings", None, "show_leaves_of_all_department_members_in_calendar")):
		from frappe.desk.reportview import build_match_conditions
		match_conditions = build_match_conditions("Leave Application")

		if match_conditions:
			conditions.append(match_conditions)

	query = """select name, from_date, to_date, employee_name, half_day,
		status, employee, docstatus
		from `tabLeave Application` where
		from_date <= %(end)s and to_date >= %(start)s <= to_date
		and docstatus < 2
		and status!='Rejected' """

	if conditions:
		query += ' and ' + ' and '.join(conditions)

	if filter_conditions:
		query += filter_conditions

	for d in frappe.db.sql(query, {"start":start, "end": end}, as_dict=True):
		e = {
			"name": d.name,
			"doctype": "Leave Application",
			"from_date": d.from_date,
			"to_date": d.to_date,
			"docstatus": d.docstatus,
			"color": d.color,
			"title": cstr(d.employee_name) + (' ' + _('(Half Day)') if d.half_day else ''),
		}
		if e not in events:
			events.append(e)

def add_block_dates(events, start, end, employee, company):
	# block days
	from erpnext.hr.doctype.leave_block_list.leave_block_list import get_applicable_block_dates

	cnt = 0
	block_dates = get_applicable_block_dates(start, end, employee, company, all_lists=True)

	for block_date in block_dates:
		events.append({
			"doctype": "Leave Block List Date",
			"from_date": block_date.block_date,
			"to_date": block_date.block_date,
			"title": _("Leave Blocked") + ": " + block_date.reason,
			"name": "_" + str(cnt),
		})
		cnt+=1

def add_holidays(events, start, end, employee, company):
	applicable_holiday_list = get_holiday_list_for_employee(employee, company)
	if not applicable_holiday_list:
		return

	for holiday in frappe.db.sql("""select name, holiday_date, description
		from `tabHoliday` where parent=%s and holiday_date between %s and %s""",
		(applicable_holiday_list, start, end), as_dict=True):
			events.append({
				"doctype": "Holiday",
				"from_date": holiday.holiday_date,
				"to_date":  holiday.holiday_date,
				"title": _("Holiday") + ": " + cstr(holiday.description),
				"name": holiday.name
			})

@frappe.whitelist()
def get_mandatory_approval(doctype):
	mandatory = ""
	if doctype == "Leave Application":
		mandatory = frappe.db.get_single_value('HR Settings',
				'leave_approver_mandatory_in_leave_application')
	else:
		mandatory = frappe.db.get_single_value('HR Settings',
				'expense_approver_mandatory_in_expense_claim')

	return mandatory

def get_approved_leaves_for_period(employee, leave_type, from_date, to_date):
	query = """
		select employee, leave_type, from_date, to_date, total_leave_days
		from `tabLeave Application`
		where employee=%(employee)s
			and docstatus=1
			and (from_date between %(from_date)s and %(to_date)s
				or to_date between %(from_date)s and %(to_date)s
				or (from_date < %(from_date)s and to_date > %(to_date)s))
	"""
	if leave_type:
		query += "and leave_type=%(leave_type)s"

	leave_applications = frappe.db.sql(query,{
		"from_date": from_date,
		"to_date": to_date,
		"employee": employee,
		"leave_type": leave_type
	}, as_dict=1)

	leave_days = 0
	for leave_app in leave_applications:
		if leave_app.from_date >= getdate(from_date) and leave_app.to_date <= getdate(to_date):
			leave_days += leave_app.total_leave_days
		else:
			if leave_app.from_date < getdate(from_date):
				leave_app.from_date = from_date
			if leave_app.to_date > getdate(to_date):
				leave_app.to_date = to_date

			leave_days += get_number_of_leave_days(employee, leave_type,
				leave_app.from_date, leave_app.to_date)

	return leave_days

@frappe.whitelist()
def get_leave_approver(employee):
	leave_approver, department = frappe.db.get_value("Employee",
		employee, ["leave_approver", "department"])

	if not leave_approver and department:
		leave_approver = frappe.db.get_value('Department Approver', {'parent': department,
			'parentfield': 'leave_approvers', 'idx': 1}, 'approver')

	return leave_approver
	
def validate_restrict_backdated_leave_application(self):
	backdate = frappe.db.get_single_value("Leave Management Settings", "restrict_backdated_leave_application")
	today = date.today()
	attn_list = frappe.db.sql("""select Count(name), employee, status, attendance_date from `tabAttendance` where status = %s and employee = %s""",('Absent',self.employee),as_dict=1)
	
	cur_date = date.today()
	leave_days = date_diff(self.to_date, self.from_date)
	###If employee is absent not applied leave and after he came to office  and aplly leave then this validation calls
	d = getdate(self.from_date) + timedelta(days=15)
	if d < cur_date and "HR Manager" not in frappe.get_roles(frappe.session.user):
		frappe.throw('Backdated Leave Application should be apply before 15 days')
		
	if backdate == 1 and getdate(self.from_date) < today and "HR Manager" not in frappe.get_roles(frappe.session.user):
		frappe.throw("Backdated can't be selected ")

@frappe.whitelist()
def validate_maternity_leave(leave_type, from_date, employee):
	gender, marital_status = frappe.db.get_value("Employee",employee, ["gender", "marital_status"])
	
	if leave_type == "Maternity Leave" and gender == "Female" and marital_status == "Married":
		td = getdate(from_date) + timedelta(85-1)
		d = getdate(from_date) + timedelta(83)
		to_date = d
		total_leave_days = date_diff(td, from_date)
		
		return [to_date,total_leave_days]
def validate_maximum_continuous_days(self):
	maxdays = frappe.db.get_single_value("Leave Management Settings", "maximum_continuous_days")
	d = date.today() + timedelta(days=maxdays)
	#self.from_date = d

	###Total No of leaves should not exceed maxdays
	if self.total_leave_days > maxdays and "HR Manager" not in frappe.get_roles(frappe.session.user):
		frappe.throw("Can't exceed "  +str(maxdays)+  " days")

	days_diff = date_diff(d,self.from_date)
	###Future planned leave validation
	if maxdays < days_diff and self.medical_reason == 0 and "HR Manager" not in frappe.get_roles(frappe.session.user):
		frappe.throw("Need to apply " + str(maxdays) +  " days before")

###If employee in notice period then this validation method will call
def validate_notice_period(self):
	res_date,accp_date,notice_period = frappe.db.get_value("Employee",self.employee, ["resignation_letter_date", "last_working_day","notice_period_served"])
	
	if notice_period == 'Yes':
		np_leave = frappe.db.get_single_value("Leave Management Settings","np_leave")
		###Based on settings leave period days validation will throw
		d = date_diff(self.to_date,self.from_date)
		if int(d) > int(np_leave):
			frappe.throw('Total No of Days Exceed the Notice Period Leave Days '+ np_leave)
		
@frappe.whitelist()		
def weakly_leave_alert():
	today = date.today()
	print(str(today))
	offset = (2-today.weekday()) % 7
	print('offset '+str(offset))
	wednesday = today + timedelta(days=offset)
	print(str(wednesday))

	if today == wednesday:
		print('in if')
		act_leave_application = frappe.db.sql(""" select name,leave_approver, employee_name from `tabLeave Application` 
			where status=%s """, ("Open"), as_dict=True)
		print('la '+str(act_leave_application))
		

		for i in act_leave_application:
			parent_doc = frappe.get_doc('Leave Application', i.name)
			args = parent_doc.as_dict()
			email_template = frappe.get_doc("Email Template", 'Weekly Leave Approval')
			message = frappe.render_template(email_template.response, args)

			parent_doc.notify({
				# for post in messages
				"message": message,
				"message_to": i.leave_approver,
				# for email
				"subject": email_template.subject,
				"notify": i.leave_approver
		})

@frappe.whitelist()
def payroll_cutoff_notify():
	receiver = []
	import datetime
	cur_date = date.today()
	print(str(cur_date))
	x = cur_date.strftime("%d")
	print(str(x))
	
	payroll_days = frappe.db.get_single_value("Leave Management Settings", "payroll_cut_off_date")
	print(str(payroll_days))

	if x == payroll_days:
		role_list = frappe.db.sql("""select name from `tabUser` where enabled = %s""",('1'),as_dict=1)
		print('role '+str(role_list))
	
		for j in role_list:
			mail = frappe.get_all("Has Role",filters={"parent":j.name, "role": "HR Manager", "parenttype": "User"},fields="parent")
			for k in mail:
				if ((k.parent not in receiver) and (k.parent != 'Administrator')):
					receiver.append(k.parent)

		act_leave_application = frappe.db.sql(""" select name, employee_name, status, leave_approver from `tabLeave Application` 
			where status=%s """, ("Open"))

		print('la '+str(act_leave_application))


		subj = 'Payroll Cutoff Notification Reminder'
		content = 'Following Leave Application is Pending for Your Approval:'
		content = """
				<table style="margin-left: auto; margin-right: auto;" border: 1px solid black>
				<tbody>
				<tr>
				<td>Record Name</td>
				<td>Employee Name</td>
				<td>Status</td>
				<td>Leave Approver</td>
				</tr>"""
		for i in act_leave_application:
			content = content +"""<tr>
				<td>"""+i[0]+"""</td>
				<td>"""+i[1]+"""</td>
				<td>"""+i[2]+"""</td>
				<td>"""+i[3]+"""</td>
				</tr>"""
		content = content + """ </tbody>
				</table>"""
	
		for n in receiver:
			frappe.sendmail(n,subject=subj,\
				message = content)
