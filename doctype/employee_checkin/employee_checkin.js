// Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Employee Checkin', {
	setup: (frm) => {
		if(!frm.doc.time) {
			frm.set_value("time", frappe.datetime.now_datetime());
		}
	},
	/*log_type: function(frm){
		if(frm.doc.log_type == 'OUT'){
			frm.doc.exit_date_time = '';
			cur_frm.refresh_fields();
		}
		if(frm.doc.log_type == 'IN'){
			frm.doc.time = '';
			cur_frm.refresh_fields();
		}
	}*/
	refresh: function(frm){
		/*if(frm.doc.log_in == 1){cur_frm.set_df_property('log_in','read_only',1);cur_frm.refresh_fields();}
		if(frm.doc.time){cur_frm.set_df_property('time','read_only',1);cur_frm.refresh_fields();}*/

		if (frm.doc.log_in == 1){
			cur_frm.set_df_property("log_in", "set_only_once", 1);
			cur_frm.refresh_fields();
		}
		if (frm.doc.entry_date_time) {
			cur_frm.set_df_property("entry_date_time", "set_only_once", 1);
			cur_frm.refresh_fields();
		}
		if (frm.doc.log_out == 1){
			cur_frm.set_df_property("log_out", "set_only_once", 1);
			cur_frm.refresh_fields();
		}
		if (frm.doc.exit_dt_time) {
			cur_frm.set_df_property("exit_dt_time", "set_only_once", 1);
			cur_frm.refresh_fields();
		}
	}
});
