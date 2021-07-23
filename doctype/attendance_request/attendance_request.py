# -*- coding: utf-8 -*-
# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, add_days, getdate
from erpnext.hr.doctype.employee.employee import is_holiday
from frappe.utils import cint, cstr, date_diff, flt, formatdate, getdate, get_link_to_form, \
	comma_or, get_fullname, add_days, nowdate, get_datetime_str
from erpnext.hr.utils import validate_dates
from datetime import date, datetime
from datetime import timedelta, date

class AttendanceRequest(Document):
	def validate(self):
		#validate_dates(self, self.from_date, self.to_date)
		if self.half_day:
			if not getdate(self.from_date)<=getdate(self.half_day_date)<=getdate(self.to_date):
				frappe.throw(_("Half day date should be in between from date and to date"))

		###Backdated Request cannot be selected
		backdate = frappe.db.get_single_value("Leave Management Settings", "restrict_backdated_leave_application")
		today = date.today()
		if backdate == 1 and getdate(self.from_date) < today and "HR Manager" not in frappe.get_roles(frappe.session.user):
			frappe.throw("Backdated can't be selected ")

		###Backdated Limit Validation
		backdate_limit = frappe.db.get_single_value("Leave Management Settings", 'backdated_limit')
		if backdate == 0:
			date_val = frappe.db.sql("""select Count(name), posting_date, from_date from `tabAttendance Request` where 
				MONTH(posting_date) = MONTH(%s) and employee = %s """,(self.posting_date,self.employee))
			frappe.msgprint('date_val '+str(date_val))
			
			for i in date_val:
				if i[0] > backdate_limit:
					frappe.throw('Backdated Entries Crossed the Limit days '+str(backdate_limit))

		###Future Attendance Request entry validation should not cross value that mentioned in settings
		futrdays = frappe.db.get_single_value("Leave Management Settings", "future_days_at_req")
		d = today + timedelta(days=futrdays)
		frappe.msgprint('FUT '+str(futrdays))
		
		###Total No of leaves should not exceed maxdays
		#if self.total_leave_days > maxdays and "HR Manager" not in frappe.get_roles(frappe.session.user):
		#	frappe.throw("Can't exceed "  +str(maxdays)+  " days")

		days_diff = date_diff(d,self.from_date)
		frappe.msgprint('DAYS DI '+str(abs(days_diff)))
		###Future planned leave validation
		if futrdays < (abs(days_diff)):
			frappe.throw("Future date entries can be for maximum of  " + str(futrdays) +  " days only.")

		###custom code #On Submit of Attendance Request notification mail sent to leave approver
		if self.workflow_state == 'Submitted':
			parent_doc = frappe.get_doc('Attendance Request', self.name)
			args = parent_doc.as_dict()
			
			la_approver = frappe.db.get_value('Employee',self.employee,'leave_approver')
			email_template = frappe.get_doc("Email Template", 'Attendance Request Approval')
			message = frappe.render_template(email_template.response, args)

			self.notify({
				# for post in messages
				"message": message,
				"message_to": la_approver,
				# for email
				"subject": email_template.subject
			})

		###Approval of attendance request record send mail to corresponding employee who applied the attendance request
		if self.workflow_state == 'Approved':
			subj = 'Attendance Request Approved Notification '
			notification_message = 'Attendance Request has been Approved - <a href="desk#Form/Attendance Request/{0}" target="_blank">{1}</a> \
				for employee  {2} .'.format(self.name, self.name, self.employee_name)
			mail = frappe.db.get_value('Employee',self.employee,'user_id')
			frappe.sendmail(mail,subject=subj,\
					message = notification_message)

		if self.workflow_state == 'Rejected':
			subj = 'Attendance Request Rejected Notification '
			notification_message = 'Attendance Request has been Rejected - <a href="desk#Form/Attendance Request/{0}" target="_blank">{1}</a> \
				for employee  {2} .'.format(self.name, self.name, self.employee_name)
			mail = frappe.db.get_value('Employee',self.employee,'user_id')
			frappe.sendmail(mail,subject=subj,\
					message = notification_message)

	def notify(self, args):
		args = frappe._dict(args)
		# args -> message, message_to, subject
		if cint(self.follow_via_email):
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

	def on_submit(self):
		self.create_attendance()

	def on_cancel(self):
		attendance_list = frappe.get_list("Attendance", {'employee': self.employee, 'attendance_request': self.name})
		if attendance_list:
			for attendance in attendance_list:
				attendance_obj = frappe.get_doc("Attendance", attendance['name'])
				attendance_obj.cancel()

	def create_attendance(self):
		request_days = date_diff(self.to_date, self.from_date) + 1
		for number in range(request_days):
			attendance_date = add_days(self.from_date, number)
			skip_attendance = self.validate_if_attendance_not_applicable(attendance_date)
			if not skip_attendance:
				attendance = frappe.new_doc("Attendance")
				attendance.employee = self.employee
				attendance.employee_name = self.employee_name
				if self.half_day and date_diff(getdate(self.half_day_date), getdate(attendance_date)) == 0:
					attendance.status = "Half Day"
				elif self.reason == "Work From Home":
					attendance.status = "Work From Home"
				elif self.reason == "On Duty":
					attendance.status = "Absent"
					attendance.absence_type = "On Duty"
					attendance.on_duty_reasons = self.on_duty_reasons
					attendance.vessel_visit_type = self.vessel_visit_type
					attendance.des = self.explanation
					#frappe.throw('test '+str(attendance.vessel_visit_type) + ' dfgg '+str(attendance.des))
				else:
					attendance.status = "Present"
				attendance.attendance_date = attendance_date
				attendance.company = self.company
				attendance.attendance_request = self.name
				attendance.save(ignore_permissions=True)
				attendance.submit()

	def validate_if_attendance_not_applicable(self, attendance_date):
		# Check if attendance_date is a Holiday
		if is_holiday(self.employee, attendance_date):
			frappe.msgprint(_("Attendance not submitted for {0} as it is a Holiday.").format(attendance_date), alert=1)
			return True

		# Check if employee on Leave
		leave_record = frappe.db.sql("""select half_day from `tabLeave Application`
			where employee = %s and %s between from_date and to_date
			and docstatus = 1""", (self.employee, attendance_date), as_dict=True)
		if leave_record:
			frappe.msgprint(_("Attendance not submitted for {0} as {1} on leave.").format(attendance_date, self.employee), alert=1)
			return True

		return False

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
		act_leave_application = frappe.db.sql(""" select name, employee, employee_name from `tabAttendance Request` 
			where workflow_state=%s """, ("Submitted"), as_dict=True)
		print('la '+str(act_leave_application))
		

		for i in act_leave_application:
			parent_doc = frappe.get_doc('Attendance Request', i.name)
			args = parent_doc.as_dict()
			la_approver = frappe.db.get_value("Employee",parent_doc.employee,'leave_approver')
			email_template = frappe.get_doc("Email Template", 'Weekly Attendance Request Approval')
			message = frappe.render_template(email_template.response, args)

			parent_doc.notify({
				# for post in messages
				"message": message,
				"message_to": la_approver,
				# for email
				"subject": email_template.subject,
				"notify": i.leave_approver
		})


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
		attn_req = frappe.db.sql(""" select name, employee, employee_name, docstatus from `tabAttendance Request` 
			where docstatus=%s """, ("0"))

		print('la '+str(attn_req))
		

		subj = 'Payroll Cutoff Notification Reminder'
		content = 'Following Attendance Request is Pending for Your Approval:'
		content = """
				<table style="margin-left: auto; margin-right: auto;" border: 1px solid black>
				<tbody>
				<tr>
				<td>Name</td>
				<td>Employee Name</td>
				</tr>"""
		for i in attn_req:
			content = content +"""<tr>
				<td>"""+i[0]+"""</td>
				<td>"""+i[2]+"""</td>
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