# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
from __future__ import unicode_literals
from os import rmdir
import frappe
from frappe.utils import now, cint, get_datetime
from frappe import _, msgprint
from frappe.utils import data, flt

def execute(filters=None):
	if not filters: filters = {}
	columns, department, chart = [], [], []
	columns = get_columns()
	data = get_employees(filters)
	chart = get_chart_data(data,filters)
	
	return columns, data, None, chart

def get_columns():
	return [
		_("Employee Name") + ":Data:140",_("Date") + ":Date:100",_("In time")+ ":Data:140", 
		_("Out time") + ":Data:140",_("Late in") + ":Data:140",
		_("Early in") + ":Data:140",
		_("Hours worked") + ":Data:140",
		_("Standard Hours") + ":Data:140",
		_("Difference") + ":int:1",
		_("Remarks") + ":Data:140",
		
	]

def get_employees(filters):
	#conditions = get_conditions(filters)
	company = (filters.get('company'))
	employee = (filters.get('employee'))
	remarks = (filters.get('remarks'))
	d = []
	
	if company and not employee and not remarks:
		ec = frappe.db.sql("""select ec.employee_name as 'emp_name',  at.attendance_date as 'attn_date',
				ec.entry_date_time as 'time', ec.exit_dt_time as 'out_time', at.late_entry as 'le', at.early_exit as 'ex',
				TIMEDIFF(ec.exit_dt_time,ec.entry_date_time) as 'hrs', ABS(TIMESTAMPDIFF(HOUR,ec.exit_dt_time,ec.entry_date_time)) as 'hour',  ABS(TIMESTAMPDIFF(HOUR,ec.exit_dt_time,ec.entry_date_time))-st.total_working_hours as 'diff', st.total_working_hours as 'twh', at.status as 'rm'
							from
								`tabEmployee Checkin` as ec, `tabShift Type` as st, `tabAttendance` as at
							where
							
								at.company = %s and
								at.employee = ec.employee and
								at.attendance_date = ec.check_in_date""",(company),as_dict=1)
		
		if ec:
			for i in ec:
				
				d.append([i.emp_name, i.attn_date, i.time, i.out_time, i.le, i.ex, i.hrs, i.twh, i.diff, i.rm])

	if company and employee and not remarks:
		el = frappe.db.sql("""select ec.employee_name as 'emp_name',  at.attendance_date as 'attn_date',
				ec.time as 'time', ec.exit_date_time as 'out_time', at.late_entry as 'le', at.early_exit as 'ex',
					TIMEDIFF(ec.exit_dt_time,ec.entry_date_time) as 'hrs', ABS(TIMESTAMPDIFF(HOUR,ec.exit_dt_time,ec.entry_date_time)) as 'hour',  ABS(TIMESTAMPDIFF(HOUR,ec.exit_dt_time,ec.entry_date_time))-st.total_working_hours as 'diff', st.total_working_hours as 'twh', at.status as 'rm'
							from
								`tabEmployee Checkin` as ec, `tabShift Type` as st, `tabAttendance` as at
								
							where
							
								at.company = %s and
								ec.employee = %s and
								at.employee = ec.employee and
								at.attendance_date = ec.check_in_date""",(company,employee),as_dict=1)
		
		if el:
			for j in el:
				
				d.append([j.emp_name, j.attn_date, j.time, j.out_time, j.le, j.ex, j.hrs, j.twh, j.diff, j.rm])
		

	if company and not employee and remarks:
		rm = frappe.db.sql("""select ec.employee_name as 'emp_name',  at.attendance_date as 'attn_date',
				ec.time as 'time', ec.exit_date_time as 'out_time', at.late_entry as 'le', at.early_exit as 'ex',
					TIMEDIFF(ec.exit_dt_time,ec.entry_date_time) as 'hrs', ABS(TIMESTAMPDIFF(HOUR,ec.exit_dt_time,ec.entry_date_time)) as 'hour',  ABS(TIMESTAMPDIFF(HOUR,ec.exit_dt_time,ec.entry_date_time))-st.total_working_hours as 'diff', st.total_working_hours as 'twh', at.status as 'rm'
							from
								`tabEmployee Checkin` as ec, `tabShift Type` as st, `tabAttendance` as at
								
							where
							
								at.company = %s and
								at.status = %s and
								at.employee = ec.employee and
								at.attendance_date = ec.check_in_date""",(company,remarks),as_dict=1)
		
		if rm:
			for l in rm:
				
				d.append([l.emp_name, l.attn_date, l.time, l.out_time, l.le, l.ex, l.hrs, l.twh, l.diff, l.rm])
		


	if company and employee and remarks:
		st = frappe.db.sql("""select ec.employee_name as 'emp_name',  at.attendance_date as 'attn_date',
				ec.time as 'time', ec.exit_date_time as 'out_time', at.late_entry as 'le', at.early_exit as 'ex',
					TIMEDIFF(ec.exit_dt_time,ec.entry_date_time) as 'hrs', ABS(TIMESTAMPDIFF(HOUR,ec.exit_dt_time,ec.entry_date_time)) as 'hour',  ABS(TIMESTAMPDIFF(HOUR,ec.exit_dt_time,ec.entry_date_time))-st.total_working_hours as 'diff', st.total_working_hours as 'twh', at.status as 'rm'
							from
								`tabEmployee Checkin` as ec, `tabShift Type` as st, `tabAttendance` as at
								
							where
							
								at.company = %s and
								ec.employee = %s and
								at.status = %s and
								at.employee = ec.employee and
								at.attendance_date = ec.check_in_date""",(company,employee,remarks),as_dict=1)
		
		if st:
			for k in st:
				
				d.append([k.emp_name, k.attn_date, k.time, k.out_time, k.le, k.ex, k.hrs, k.twh, k.diff, k.rm])
	
	return d

def get_chart_data(data,filters):
	
	val = []
	labels = [d[0] for d in data]
	frappe.msgprint('lbl '+str(labels))
	val = [d[7] for d in data]
	frappe.msgprint('val '+str(val))
	datasets = []
	datasets.append({
		'name': 'Average',
		'values': val,
		"valuesOverPoints":0
	})


	chart = {
		"data": {
			'labels': labels,
			'datasets': datasets
		}
	}
	chart["type"] = "bar"
	chart["height"] = 400
	chart["colors"] = ['red']
	#chart["title"] = "No Of " + filters.get('doc_type') + " Vs " + filters.get('trend_category') 
	chart["title"] = 'Attendance Report'
	return chart

