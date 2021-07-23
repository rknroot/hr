// Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Shift Type', {
	refresh: function(frm) {
		frm.add_custom_button(
			'Mark Attendance',
			() => frm.call({
				doc: frm.doc,
				method: 'process_auto_attendance',
				freeze: true,
				callback: () => {
					frappe.msgprint(__("Attendance has been marked as per employee check-ins"));
				}
			})
		);
	},
	break_hours: function(frm){
        if(frm.doc.break_hours && frm.doc.no == '1'){
        var a = moment([frm.doc.start_time] , "HH:mm");
        var b = moment([frm.doc.end_time] , "HH:mm");
        console.log(b.diff(a, 'hours'))
        var c = b.diff(a, 'hours');
        var res = (c - frm.doc.break_hours);
        console.log(res);
		cur_frm.set_value('total_working_hours',res);
		cur_frm.refresh_fields();
		}
    },
	yes: function(frm){
		if(frm.doc.yes == '1'){
			var a = moment([frm.doc.start_time] , "HH:mm");
       		var b = moment([frm.doc.end_time] , "HH:mm");
        	console.log(b.diff(a, 'hours'))
        	var c = b.diff(a, 'hours');
			cur_frm.set_value('total_working_hours',c);
			frm.doc.no = '';
			frm.doc.break_hours = '';
			cur_frm.refresh_fields();
		}
		else{
			frm.doc.total_working_hours = '';
			cur_frm.refresh_fields();
		}
	},
	no: function(frm){
		if(!frm.doc.no){
			frm.doc.break_hours = '';
			frm.doc.total_working_hours = '';
			cur_frm.refresh_fields();
		}
	}

});
