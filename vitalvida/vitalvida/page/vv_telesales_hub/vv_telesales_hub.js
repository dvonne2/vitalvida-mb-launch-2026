frappe.pages['vv-telesales-hub'].on_page_load = function(wrapper) {
    // Create the page
    frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Telesales Hub',
        single_column: true
    });

    // Hide default header
    $(wrapper).find('.page-head').hide();

    // Load HTML template from server
    frappe.call({
        method: 'frappe.client.get_template',
        args: { template_name: 'vv_telesales_hub' },
        callback: function(r) {
            if(r.message) {
                // Inject HTML into page
                $(wrapper).find('.layout-main-section').html(r.message);
                // Initialize logic after DOM ready
                initVV(wrapper);
            } else {
                $(wrapper).find('.layout-main-section').html('<div style="padding:20px;color:red;">Template failed to load.</div>');
            }
        }
    });

    function initVV(wrapper) {
        var page = wrapper.page;
        var curP = 'cn', curA = null, curId = null, curPer = 'd';
        var curCloser = null, allOrders = {}, daList = [], timers = {};

        // ---------------- HELPERS ----------------
        function fmt(n) { if(!n) return '₦0'; return '₦'+parseFloat(n).toLocaleString('en-NG'); }
        function shortAddr(a) { if(!a) return '—'; var p=a.split(','); return p.slice(-2).join(',').trim(); }
        function agentName() { var el = document.getElementById('vv-agent-name'); return el ? el.textContent : 'Agent'; }

        // ---------------- LOADERS ----------------
        function loadCloser() {
            frappe.call({
                method: 'frappe.client.get_list',
                args: {
                    doctype: 'Telesales Closer',
                    filters: [['user','=',frappe.session.user]],
                    fields: ['name','closer_name','phone'],
                    limit: 1
                },
                callback: function(r) {
                    if(r.message && r.message.length) {
                        curCloser = r.message[0];
                        safeSet('vv-agent-name', curCloser.closer_name);
                    } else {
                        safeSet('vv-agent-name', frappe.session.user);
                    }
                    loadAllOrders();
                    loadDAs();
                }
            });
        }

        function loadAllOrders() {
            var repFilter = curCloser ? curCloser.name : frappe.session.user;
            frappe.call({
                method: 'frappe.client.get_list',
                args: {
                    doctype: 'VV Order',
                    filters: [['telesales_rep','=',repFilter]],
                    fields: ['name','customer_name','address','order_status','package_name','total_payable','delivery_agent','creation','modified','paid_at'],
                    limit: 200,
                    order_by: 'creation desc'
                },
                callback: function(r) {
                    if(r.message) {
                        organizeOrders(r.message);
                        updateStats(r.message);
                        renderCurrent();
                    }
                }
            });
        }

        function loadDAs() {
            frappe.call({
                method: 'frappe.client.get_list',
                args: {
                    doctype: 'Delivery Agent',
                    filters: [['is_active','=',1]],
                    fields: ['name','agent_name','phone'],
                    limit: 50
                },
                callback: function(r) {
                    if(r.message) daList = r.message;
                }
            });
        }

        // ---------------- ORGANIZE ----------------
        function organizeOrders(orders) {
            allOrders = { cn:[], cf:[], ow:[], cb:[], dn:[] };
            orders.forEach(function(o) {
                var s = o.order_status;
                if(s==='Pending') allOrders.cn.push(o);
                else if(s==='Confirmed') allOrders.cf.push(o);
                else if(s==='Assigned'||s==='Out for Delivery') allOrders.ow.push(o);
                else if(s==='Rescheduled') allOrders.cb.push(o);
                else if(s==='Delivered'||s==='Paid') allOrders.dn.push(o);
            });
            updateCounts();
        }

        function updateCounts() {
            ['cn','cf','ow','cb','dn'].forEach(function(k) {
                safeSet('vv-cnt-'+k, (allOrders[k]||[]).length);
            });
        }

        function updateStats(orders) {
            var paid = orders.filter(o => o.order_status === 'Paid');
            var earned = paid.reduce((s,o)=>s+(o.total_payable||0),0);
            safeSet('vv-earned', fmt(earned));
            safeSet('vv-closed', orders.length);
            safeSet('vv-rate', orders.length ? Math.round((paid.length/orders.length)*100)+'%' : '0%');
        }

        // ---------------- RENDER ----------------
        window.vvGo = function(p) {
            curP = p;
            document.querySelectorAll('.vv-page').forEach(x=>x.classList.remove('active'));
            document.querySelectorAll('.vv-tab').forEach(x=>x.classList.toggle('active',x.dataset.p===p));
            var page = document.getElementById('vv-page-'+p);
            if(page) page.classList.add('active');
            render(p);
        };

        function renderCurrent() { render(curP); }

        function render(p) {
            if(p==='cn') rCN();
            else if(p==='cf') rCF();
            else if(p==='ow') rOW();
            else if(p==='cb') rCB();
            else if(p==='dn') rDN();
        }

        function rCN() {
            var el = document.getElementById('vv-cn-list');
            if(!el) return;
            var orders = allOrders.cn || [];
            if(!orders.length){
                el.innerHTML = '<div style="padding:20px">No orders</div>';
                return;
            }
            el.innerHTML = orders.map(o=>{
                return `<div style="padding:10px;border-bottom:1px solid #ddd">
                          <strong>${o.customer_name}</strong><br>${fmt(o.total_payable)}
                        </div>`;
            }).join('');
        }

        function rCF() { safeHTML('vv-cf-list','Loading confirmed...'); }
        function rOW() { safeHTML('vv-ow-list','Loading deliveries...'); }
        function rCB() { safeHTML('vv-cb-list','Loading callbacks...'); }
        function rDN() { safeHTML('vv-dn-list','Loading done...'); }

        // ---------------- SAFE HELPERS ----------------
        function safeSet(id, val){
            var el=document.getElementById(id);
            if(el) el.textContent = val;
        }

        function safeHTML(id, val){
            var el=document.getElementById(id);
            if(el) el.innerHTML = val;
        }

        // ---------------- INIT ----------------
        loadCloser();
        setInterval(loadAllOrders, 60000);
    }
};
