# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
from calendar import month
from erpnext.hr.doctype.employee.employee import deactivate_sales_person
import frappe

from frappe.utils import getdate, nowdate
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, date_diff, flt, formatdate, getdate, get_link_to_form, \
	comma_or, get_fullname, add_days, nowdate, get_datetime_str
from frappe.utils import cstr, get_datetime, formatdate
from datetime import timedelta, date

class Attendance(Document):
	def validate(self):
		from erpnext.controllers.status_updater import validate_status
		validate_status(self.status, ["Present", "Absent", "On Leave", "Half Day", "Work From Home"])
		self.validate_attendance_date()
		self.validate_duplicate_record()
		self.check_leave_record()
		###Custom code for sending notification mail to reporting manager to approve the attendance record. once record gets submitted
		self.notification()

	def notification(self):
		if self.workflow_state == 'Submitted':
			parent_doc = frappe.get_doc('Attendance', self.name)
			args = parent_doc.as_dict()
			
			#template = frappe.db.get_single_value('HR Settings', 'leave_approval_notification_template')
			#if not template:
			#	frappe.msgprint(_("Please set default template for Leave Approval Notification in HR Settings."))
			#	return
			la_approver = frappe.db.get_value('Employee',self.employee,'leave_approver')
			frappe.msgprint('la '+str(la_approver))
			email_template = frappe.get_doc("Email Template", 'Attendance Regularization Approval')
			message = frappe.render_template(email_template.response, args)

			self.notify({
				# for post in messages
				"message": message,
				"message_to": la_approver,
				# for email
				"subject": email_template.subject
			})

	def notify(self, args):
		print('called')
		args = frappe._dict(args)
		# args -> message, message_to, subject
		if cint(self.follow_via_email):
			frappe.msgprint('in')
			contact = args.message_to
			if not isinstance(contact, list):
				frappe.msgprint('inside')
				if not args.notify == "employee":
					frappe.msgprint('emp')
					contact = frappe.get_doc('User', contact).email or contact

			sender      	    = dict()
			sender['email']     = frappe.get_doc('User', frappe.session.user).email
			sender['full_name'] = frappe.utils.get_fullname(sender['email'])

			try:
				frappe.msgprint('em')
				frappe.sendmail(
					recipients = contact,
					sender = sender['email'],
					subject = args.subject,
					message = args.message,
				)
				frappe.msgprint(_("Email sent to {0}").format(contact))
			except frappe.OutgoingEmailError:
				frappe.msgprint('excp')
				pass


	def validate_attendance_date(self):
		date_of_joining = frappe.db.get_value("Employee", self.employee, "date_of_joining")

		# leaves can be marked for future dates
		if self.status in ('Absent','Present','Half Day', 'Quarter Day','Work From Home') and self.absence_type != 'On Duty' and not self.leave_application and getdate(self.attendance_date) > getdate(nowdate()):
			frappe.msgprint('in '+str(self.status))
			frappe.throw(_("Attendance can not be marked for future dates"))
		elif date_of_joining and getdate(self.attendance_date) < getdate(date_of_joining):
			frappe.throw(_("Attendance date can not be less than employee's joining date"))

	def validate_duplicate_record(self):
		res = frappe.db.sql("""
			select name from `tabAttendance`
			where employee = %s
				and attendance_date = %s
				and name != %s
				and docstatus != 2
		""", (self.employee, getdate(self.attendance_date), self.name))
		if res:
			frappe.throw(_("Attendance for employee {0} is already marked for the date {1}").format(
				frappe.bold(self.employee), frappe.bold(self.attendance_date)))

	def check_leave_record(self):
		leave_record = frappe.db.sql("""
			select leave_type, half_day, half_day_date
			from `tabLeave Application`
			where employee = %s
				and %s between from_date and to_date
				and status = 'Approved'
				and docstatus = 1
		""", (self.employee, self.attendance_date), as_dict=True)
		if leave_record:
			for d in leave_record:
				self.leave_type = d.leave_type
				if d.half_day_date == getdate(self.attendance_date):
					self.status = 'Half Day'
					frappe.msgprint(_("Employee {0} on Half day on {1}")
						.format(self.employee, formatdate(self.attendance_date)))
				else:
					self.status = 'On Leave'
					frappe.msgprint(_("Employee {0} is on Leave on {1}")
						.format(self.employee, formatdate(self.attendance_date)))

		if self.status in ("On Leave", "Half Day"):
			if not leave_record:
				frappe.msgprint(_("No leave record found for employee {0} on {1}")
					.format(self.employee, formatdate(self.attendance_date)), alert=1)
		elif self.leave_type:
			self.leave_type = None
			self.leave_application = None

	def validate_employee(self):
		emp = frappe.db.sql("select name from `tabEmployee` where name = %s and status = 'Active'",
		 	self.employee)
		if not emp:
			frappe.throw(_("Employee {0} is not active or does not exist").format(self.employee))

@frappe.whitelist()
def get_events(start, end, filters=None):
	events = []

	employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user})

	if not employee:
		return events

	from frappe.desk.reportview import get_filters_cond
	conditions = get_filters_cond("Attendance", filters, [])
	add_attendance(events, start, end, conditions=conditions)
	return events

def add_attendance(events, start, end, conditions=None):
	query = """select name, attendance_date, status
		from `tabAttendance` where
		attendance_date between %(from_date)s and %(to_date)s
		and docstatus < 2"""
	if conditions:
		query += conditions

	for d in frappe.db.sql(query, {"from_date":start, "to_date":end}, as_dict=True):
		e = {
			"name": d.name,
			"doctype": "Attendance",
			"start": d.attendance_date,
			"end": d.attendance_date,
			"title": cstr(d.status),
			"docstatus": d.docstatus
		}
		if e not in events:
			events.append(e)

def mark_attendance(employee, attendance_date, status, shift=None, leave_type=None, ignore_validate=False):
	if not frappe.db.exists('Attendance', {'employee':employee, 'attendance_date':attendance_date, 'docstatus':('!=', '2')}):
		company = frappe.db.get_value('Employee', employee, 'company')
		attendance = frappe.get_doc({
			'doctype': 'Attendance',
			'employee': employee,
			'attendance_date': attendance_date,
			'status': status,
			'company': company,
			'shift': shift,
			'leave_type': leave_type
		})
		attendance.flags.ignore_validate = ignore_validate
		attendance.insert()
		attendance.submit()
		return attendance.name

@frappe.whitelist()
def mark_bulk_attendance(data):
	import json
	from pprint import pprint
	if isinstance(data, frappe.string_types):
		data = json.loads(data)
	data = frappe._dict(data)
	company = frappe.get_value('Employee', data.employee, 'company')
	if not data.unmarked_days:
		frappe.throw(_("Please select a date."))
		return

	for date in data.unmarked_days:
		doc_dict = {
			'doctype': 'Attendance',
			'employee': data.employee,
			'attendance_date': get_datetime(date),
			'status': data.status,
			'company': company,
		}
		attendance = frappe.get_doc(doc_dict).insert()
		attendance.submit()


def get_month_map():
	return frappe._dict({
		"January": 1,
		"February": 2,
		"March": 3,
		"April": 4,
		"May": 5,
		"June": 6,
		"July": 7,
		"August": 8,
		"September": 9,
		"October": 10,
		"November": 11,
		"December": 12
		})

@frappe.whitelist()
def get_unmarked_days(employee, month):
	print('called')
	import calendar
	month_map = get_month_map()

	today = get_datetime()

	dates_of_month = ['{}-{}-{}'.format(today.year, month_map[month], r) for r in range(1, calendar.monthrange(today.year, month_map[month])[1] + 1)]

	length = len(dates_of_month)
	month_start, month_end = dates_of_month[0], dates_of_month[length-1]


	records = frappe.get_all("Attendance", fields = ['attendance_date', 'employee'] , filters = [
		["attendance_date", ">=", month_start],
		["attendance_date", "<=", month_end],
		["employee", "=", employee],
		["docstatus", "!=", 2]
	])

	marked_days = [get_datetime(record.attendance_date) for record in records]
	unmarked_days = []

	for date in dates_of_month:
		date_time = get_datetime(date)
		if today.day == date_time.day and today.month == date_time.month:
			break
		if date_time not in marked_days:
			unmarked_days.append(date)

	return unmarked_days

@frappe.whitelist()
def payroll_cutoff_notify():
	receiver = []
	rec = []
	import datetime
	cur_date = date.today()
	print(str(cur_date))
	x = cur_date.strftime("%d")
	print(str(x))
	
	payroll_days = frappe.db.get_single_value("Leave Management Settings", "attendance_payroll_cutoff")
	print(str(payroll_days))

	if x == payroll_days:
		attn_req = frappe.db.sql(""" select name, employee, employee_name, workflow_state from `tabAttendance` 
			where docstatus=%s """, ("0"))

		print('la '+str(attn_req))
		

		subj = 'Payroll Cutoff Notification Reminder'
		content = 'Following Attendance is Pending for Your Approval:'
		content = """
				<table style="margin-left: auto; margin-right: auto;" border: 1px solid black>
				<tbody>
				<tr>
				<td>Name</td>
				<td>Employee Name</td>
				<td>Status</td>
				</tr>"""
		for i in attn_req:
			content = content +"""<tr>
				<td>"""+i[0]+"""</td>
				<td>"""+i[2]+"""</td>
				<td>"""+i[3]+"""</td>
				</tr>"""
		content = content + """ </tbody>
				</table>"""
	
		la_appr = frappe.db.get_value("Employee", i[1], 'leave_approver')
		print('APPROVER '+str(la_appr))
		rec.append(la_appr)
		for n in rec:
			print(str(n))
			frappe.sendmail(n,subject=subj,\
				message = content)


@frappe.whitelist()
def attendance_not_marked():
	from datetime import datetime
	now = datetime.now()
	cur_month = str(now.month)
	print(str(cur_month))
	
	print(str(now))
	offset = (4-now.weekday()) % 7
	print('offset '+str(offset))
	friday = now + timedelta(days=offset)
	print(str(friday))

	#if now == friday:
	if 1 == 1:
		emp_list = frappe.db.sql("""select name, employee_name, user_id from `tabEmployee` where status = %s""",('Active'),as_dict=1)
		print('EMP '+str(emp_list))
		for i in emp_list:
			import calendar
			month_map = cur_month

			today = get_datetime()

			dates_of_month = ['{}-{}-{}'.format(today.year, int(month_map), r) for r in range(1, calendar.monthrange(today.year, int(month_map))[1] + 1)]
			print('DM '+str(dates_of_month))
			length = len(dates_of_month)
			print('LEn '+str(length))
			month_start, month_end = dates_of_month[0], dates_of_month[length-1]
			print('MS '+str(month_start) + ' ME '+str(month_end))

			for j in dates_of_month:
				records = frappe.get_all("Attendance", fields = ['attendance_date', 'employee', 'name', 'follow_via_email'] , filters = [
					["attendance_date", ">=", j],
					["attendance_date", "<=", friday],
					["employee", "=", i.name],
					["docstatus", "!=", 2]
				])
				print('rec '+str(records))
				marked_days = [get_datetime(record.attendance_date) for record in records]
				print('MD '+str(marked_days))
				unmarked_days = []

				for date in dates_of_month:
					date_time = get_datetime(date)
					print('DDT '+str(date_time.day) + ' TO '+str(today.day))
					if today.day == date_time.day and today.month == date_time.month:
						break
					if date_time not in marked_days:
						unmarked_days.append(date)
					print('UD '+str(unmarked_days))

				for atn in records:
					parent_doc = frappe.get_doc('Attendance', atn.name)
					args = parent_doc.as_dict()
					email_template = frappe.get_doc("Email Template", 'Weekly absent status')
					message = frappe.render_template(email_template.response, args)

					
					parent_doc.notify({
							# for post in messages
							"message": message,
							"message_to": 'hqlaps@outlook.com',
							# for email
							"subject": email_template.subject
					})

				#	args = frappe._dict(args)
					# args -> message, message_to, subject
				#	if cint(atn.follow_via_email):
				#		contact = args.message_to
				#	if not isinstance(contact, list):
				#		if not args.notify == "employee":
				#			contact = frappe.get_doc('User', contact).email or contact

				#	sender      	    = dict()
				#	sender['email']     = frappe.get_doc('User', frappe.session.user).email
				#	sender['full_name'] = frappe.utils.get_fullname(sender['email'])

				#	try:
				#		frappe.sendmail(
				#			recipients = contact,
				#			sender = sender['email'],
				#			subject = args.subject,
				#			message = args.message,
				#		)
				#		frappe.msgprint(_("Email sent to {0}").format(contact))
				#	except frappe.OutgoingEmailError:
				#		pass
				#subj = 'Not Marked Attendance for days in a week - ' + i.employee_name
				#content = 'Following Attendance is Pending for Your Approval:'
				#content = """
				#	<table style="margin-left: auto; margin-right: auto;" border: 1px solid black>
				#	<tbody>
				#	<tr>
				#	<td>Not Marked Attendance Dates</td>
				#	</tr>"""
				#for ud in unmarked_days:
				#	content = content +"""<tr>
				#		<td>"""+ud+"""</td>
				#		</tr>"""
				#content = content + """ </tbody>
				#	</table>"""
				#print(str(content))
				#mail = frappe.db.get_value('Employee',i.name,'user_id')
				#frappe.sendmail(mail,subject=subj,\
				#	message = content)