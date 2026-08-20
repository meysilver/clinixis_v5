from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response
import sqlite3, hashlib, os, csv, io
from database import (get_db, init_db, get_dashboard_data, log_action,
                      fifo_deduct, get_product_stock, get_all_products_with_stock,
                      get_expiry_digest, get_sales_velocity, get_smart_insights,
                      get_recent_activity, get_sale_receipt, seed_hospital_products,
                      HEALTH_TIPS)
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = 'clinixis-secret-2024'
init_db()

# ── Decorators ────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def owner_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'owner':
            return render_template('access_denied.html'), 403
        return f(*args, **kwargs)
    return decorated

# ── Auth ──────────────────────────────────────────────────────
@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    error = None
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        pw = hashlib.sha256(request.form['password'].encode()).hexdigest()
        conn = get_db()
        p = conn.execute("SELECT * FROM pharmacies WHERE email=? AND password=?", (email,pw)).fetchone()
        if p:
            session.update({'user_id':p['id'],'pharmacy_id':p['id'],'pharmacy_name':p['name'],
                            'owner_name':p['owner'],'display_name':p['owner'],'role':'owner'})
            conn.close()
            return redirect(url_for('dashboard'))
        s = conn.execute("SELECT * FROM staff WHERE email=? AND password=? AND is_active=1", (email,pw)).fetchone()
        if s:
            pharmacy = conn.execute("SELECT * FROM pharmacies WHERE id=?", (s['pharmacy_id'],)).fetchone()
            session.update({'user_id':s['id'],'pharmacy_id':s['pharmacy_id'],
                            'pharmacy_name':pharmacy['name'],'owner_name':pharmacy['owner'],
                            'display_name':s['name'],'role':s['role']})
            conn.close()
            return redirect(url_for('dashboard'))
        conn.close()
        error = 'Email or password incorrect.'
    return render_template('login.html', error=error)

@app.route('/register', methods=['GET','POST'])
def register():
    error = None
    if request.method == 'POST':
        name = request.form['pharmacy_name'].strip()
        owner = request.form['owner_name'].strip()
        email = request.form['email'].strip().lower()
        pw = hashlib.sha256(request.form['password'].encode()).hexdigest()
        try:
            conn = get_db()
            conn.execute("INSERT INTO pharmacies (name,owner,email,password,phone,whatsapp,business_type) VALUES (?,?,?,?,?,?,?)",
                         (name,owner,email,pw,request.form.get('phone',''),
                          request.form.get('whatsapp',''),request.form.get('business_type','pharmacy')))
            p = conn.execute("SELECT * FROM pharmacies WHERE email=?", (email,)).fetchone()
            log_action(p['id'], p['id'], 'owner', 'ACCOUNT_CREATED', f'New business: {name}', conn=conn)
            conn.commit()
            conn.close()
            session.update({'user_id':p['id'],'pharmacy_id':p['id'],'pharmacy_name':p['name'],
                            'owner_name':p['owner'],'display_name':p['owner'],'role':'owner'})
            return redirect(url_for('dashboard'))
        except sqlite3.IntegrityError:
            error = 'This email is already registered.'
    return render_template('register.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── Dashboard ─────────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    import random
    pid = session['pharmacy_id']
    # Seed hospital products if needed
    seed_hospital_products(pid)
    data = get_dashboard_data(pid)
    velocity = get_sales_velocity(pid)
    insights = get_smart_insights(pid)
    activity = get_recent_activity(pid)
    hour = datetime.now().hour
    tip = random.choice(HEALTH_TIPS)
    return render_template('dashboard.html', data=data, velocity=velocity,
                           insights=insights, hour=hour, activity=activity, tip=tip)

# ── Receipt API ───────────────────────────────────────────────
@app.route('/sales/receipt/<int:sale_id>')
@login_required
def sale_receipt(sale_id):
    receipt = get_sale_receipt(sale_id, session['pharmacy_id'])
    if not receipt:
        return jsonify({'success': False, 'message': 'Receipt not found'}), 404
    return jsonify({'success': True, 'receipt': receipt})

# ── Sales ─────────────────────────────────────────────────────
@app.route('/sales')
@login_required
def sales():
    conn = get_db()
    all_products = get_all_products_with_stock(session['pharmacy_id'])
    products = [p for p in all_products if p['quantity'] > 0]
    recent_sales = [dict(s) for s in conn.execute(
        """SELECT s.*, COUNT(si.id) as item_count FROM sales s
           LEFT JOIN sale_items si ON s.id=si.sale_id
           WHERE s.pharmacy_id=? AND s.voided=0
           GROUP BY s.id ORDER BY s.created_at DESC LIMIT 30""",
        (session['pharmacy_id'],)).fetchall()]
    conn.close()
    return render_template('sales.html', products=products, recent_sales=recent_sales)

@app.route('/sales/record', methods=['POST'])
@login_required
def record_sale():
    data = request.get_json()
    items = data.get('items', [])
    if not items:
        return jsonify({'success': False, 'message': 'No items selected'})
    conn = get_db()
    try:
        total = sum(float(i['subtotal']) for i in items)
        staff_id = session['user_id'] if session['role'] == 'staff' else None
        cur = conn.execute(
            "INSERT INTO sales (pharmacy_id,staff_id,total_amount,note) VALUES (?,?,?,?)",
            (session['pharmacy_id'], staff_id, total, data.get('note','')))
        sale_id = cur.lastrowid

        item_names = []
        for item in items:
            deductions = fifo_deduct(conn, item['product_id'], session['pharmacy_id'], item['quantity'])
            si_cur = conn.execute(
                "INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,subtotal) VALUES (?,?,?,?,?)",
                (sale_id, item['product_id'], item['quantity'], item['unit_price'], item['subtotal']))
            sale_item_id = si_cur.lastrowid
            for batch_id, qty_used in deductions:
                conn.execute(
                    "INSERT INTO sale_batch_deductions (sale_item_id,batch_id,quantity_deducted) VALUES (?,?,?)",
                    (sale_item_id, batch_id, qty_used))
            item_names.append(f"{item['name']} x{item['quantity']}")

        log_action(session['pharmacy_id'], session['user_id'], session['role'],
                   'SALE_RECORDED', f"Sale #{sale_id}: {', '.join(item_names)} | ₦{total:,.0f}", conn=conn)
        conn.commit()
        return jsonify({'success': True, 'sale_id': sale_id, 'total': total})
    except ValueError as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)})
    finally:
        conn.close()

@app.route('/sales/void/<int:sale_id>', methods=['POST'])
@owner_required
def void_sale(sale_id):
    conn = get_db()
    sale = conn.execute("SELECT * FROM sales WHERE id=? AND pharmacy_id=?",
                        (sale_id, session['pharmacy_id'])).fetchone()
    if not sale:
        conn.close()
        return jsonify({'success': False, 'message': 'Sale not found'})
    deductions = conn.execute(
        """SELECT sbd.* FROM sale_batch_deductions sbd
           JOIN sale_items si ON sbd.sale_item_id=si.id
           WHERE si.sale_id=?""", (sale_id,)).fetchall()
    for d in deductions:
        conn.execute("UPDATE product_batches SET quantity_remaining=quantity_remaining+? WHERE id=?",
                     (d['quantity_deducted'], d['batch_id']))
    conn.execute("UPDATE sales SET voided=1 WHERE id=?", (sale_id,))
    log_action(session['pharmacy_id'], session['user_id'], session['role'],
               'SALE_VOIDED', f"Sale #{sale_id} voided | ₦{sale['total_amount']:,.0f}", conn=conn)
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ── Stock ─────────────────────────────────────────────────────
@app.route('/stock')
@login_required
def stock():
    return render_template('stock.html', products=get_all_products_with_stock(session['pharmacy_id']))

@app.route('/stock/add', methods=['POST'])
@login_required
def add_product():
    conn = get_db()
    name = request.form['name']
    conn.execute("""INSERT INTO products (pharmacy_id,name,category,unit,selling_price,low_stock_alert,supplier)
                    VALUES (?,?,?,?,?,?,?)""",
                 (session['pharmacy_id'], name, request.form.get('category','General'),
                  request.form['unit'], float(request.form['selling_price']),
                  int(request.form.get('low_stock_alert',10)), request.form.get('supplier') or None))
    prod_id = conn.execute("SELECT last_insert_rowid() as id").fetchone()['id']
    qty = int(request.form.get('quantity', 0))
    if qty > 0:
        cost = float(request.form.get('cost_price', 0))
        exp = request.form.get('expiry_date') or None
        conn.execute("""INSERT INTO product_batches
            (product_id,pharmacy_id,batch_number,cost_price,quantity_received,quantity_remaining,expiry_date)
            VALUES (?,?,?,?,?,?,?)""",
            (prod_id, session['pharmacy_id'], f"BCH-{prod_id:03d}-A", cost, qty, qty, exp))
    log_action(session['pharmacy_id'], session['user_id'], session['role'],
               'PRODUCT_ADDED', f"New product: {name}", conn=conn)
    conn.commit()
    conn.close()
    return redirect(url_for('stock'))

@app.route('/stock/add-batch', methods=['POST'])
@login_required
def add_batch():
    data = request.get_json()
    conn = get_db()
    product = conn.execute("SELECT * FROM products WHERE id=? AND pharmacy_id=?",
                           (data['product_id'], session['pharmacy_id'])).fetchone()
    if not product:
        conn.close()
        return jsonify({'success': False, 'message': 'Product not found'})
    existing = conn.execute("SELECT COUNT(*) as n FROM product_batches WHERE product_id=?",
                            (data['product_id'],)).fetchone()['n']
    suffix = chr(65 + existing)
    batch_num = data.get('batch_number') or f"BCH-{data['product_id']:03d}-{suffix}"
    conn.execute("""INSERT INTO product_batches
        (product_id,pharmacy_id,batch_number,cost_price,quantity_received,quantity_remaining,expiry_date)
        VALUES (?,?,?,?,?,?,?)""",
        (data['product_id'], session['pharmacy_id'], batch_num,
         float(data.get('cost_price', 0)), int(data['quantity']),
         int(data['quantity']), data.get('expiry_date') or None))
    log_action(session['pharmacy_id'], session['user_id'], session['role'],
               'BATCH_ADDED',
               f"{product['name']}: batch {batch_num} — {data['quantity']} units"
               + (f", exp {data['expiry_date']}" if data.get('expiry_date') else ''), conn=conn)
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/stock/edit', methods=['POST'])
@owner_required
def edit_product():
    data = request.get_json()
    conn = get_db()
    old = conn.execute("SELECT * FROM products WHERE id=? AND pharmacy_id=?",
                       (data['product_id'], session['pharmacy_id'])).fetchone()
    conn.execute("UPDATE products SET name=?,selling_price=?,low_stock_alert=?,supplier=? WHERE id=? AND pharmacy_id=?",
                 (data['name'], data['selling_price'], data['low_stock_alert'],
                  data.get('supplier'), data['product_id'], session['pharmacy_id']))
    action = 'PRICE_CHANGED' if old and old['selling_price'] != data['selling_price'] else 'PRODUCT_EDITED'
    detail = (f"{data['name']}: ₦{old['selling_price']:,.0f} → ₦{data['selling_price']:,.0f}"
              if action == 'PRICE_CHANGED' else f"Updated: {data['name']}")
    log_action(session['pharmacy_id'], session['user_id'], session['role'], action, detail, conn=conn)
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ── Expenses ──────────────────────────────────────────────────
@app.route('/expenses')
@owner_required
def expenses():
    conn = get_db()
    exps = [dict(e) for e in conn.execute(
        "SELECT * FROM expenses WHERE pharmacy_id=? ORDER BY created_at DESC LIMIT 50",
        (session['pharmacy_id'],)).fetchall()]
    month_total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) as t FROM expenses WHERE pharmacy_id=? AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now')",
        (session['pharmacy_id'],)).fetchone()['t']
    week_total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) as t FROM expenses WHERE pharmacy_id=? AND created_at>=date('now','-7 days')",
        (session['pharmacy_id'],)).fetchone()['t']
    conn.close()
    return render_template('expenses.html', expenses=exps, month_total=month_total, week_total=week_total)

@app.route('/expenses/add', methods=['POST'])
@owner_required
def add_expense():
    conn = get_db()
    desc, amt = request.form['description'], float(request.form['amount'])
    conn.execute("INSERT INTO expenses (pharmacy_id,category,description,amount) VALUES (?,?,?,?)",
                 (session['pharmacy_id'], request.form['category'], desc, amt))
    log_action(session['pharmacy_id'], session['user_id'], session['role'],
               'EXPENSE_ADDED', f"{request.form['category']}: {desc} — ₦{amt:,.0f}", conn=conn)
    conn.commit()
    conn.close()
    return redirect(url_for('expenses'))

# ── Reports ───────────────────────────────────────────────────
@app.route('/reports')
@owner_required
def reports():
    conn = get_db()
    pid = session['pharmacy_id']
    month_revenue = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) as t FROM sales WHERE pharmacy_id=? AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now') AND voided=0",
        (pid,)).fetchone()['t']
    month_tx = conn.execute(
        "SELECT COUNT(*) as n FROM sales WHERE pharmacy_id=? AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now') AND voided=0",
        (pid,)).fetchone()['n']
    month_expenses = conn.execute(
        "SELECT COALESCE(SUM(amount),0) as t FROM expenses WHERE pharmacy_id=? AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now')",
        (pid,)).fetchone()['t']
    daily_data = [dict(r) for r in conn.execute(
        "SELECT DATE(created_at) as day,SUM(total_amount) as total FROM sales WHERE pharmacy_id=? AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now') AND voided=0 GROUP BY DATE(created_at) ORDER BY day",
        (pid,)).fetchall()]
    category_breakdown = [dict(r) for r in conn.execute(
        """SELECT p.category,SUM(si.quantity) as units,SUM(si.subtotal) as revenue
           FROM sale_items si JOIN products p ON si.product_id=p.id
           WHERE p.pharmacy_id=? GROUP BY p.category ORDER BY revenue DESC""",
        (pid,)).fetchall()]
    conn.close()
    digest = get_expiry_digest(pid)
    return render_template('reports.html',
        month_revenue=month_revenue, month_tx=month_tx,
        month_expenses=month_expenses, month_profit=month_revenue-month_expenses,
        daily_data=daily_data, category_breakdown=category_breakdown, digest=digest)

@app.route('/reports/export')
@owner_required
def export_sales():
    conn = get_db()
    rows = conn.execute(
        """SELECT s.id,s.created_at,s.total_amount,s.note,
                  GROUP_CONCAT(p.name||' x'||si.quantity,'; ') as items
           FROM sales s LEFT JOIN sale_items si ON s.id=si.sale_id
           LEFT JOIN products p ON si.product_id=p.id
           WHERE s.pharmacy_id=? AND s.voided=0 GROUP BY s.id ORDER BY s.created_at DESC""",
        (session['pharmacy_id'],)).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Sale ID','Date','Total (NGN)','Items','Note'])
    for r in rows:
        writer.writerow([r['id'],r['created_at'],r['total_amount'],r['items'] or '',r['note'] or ''])
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':'attachment;filename=clinixis_sales.csv'})

@app.route('/reports/expiry-digest')
@owner_required
def expiry_digest_preview():
    return "Expiry digest coming soon.", 200
# ── Staff ─────────────────────────────────────────────────────
@app.route('/staff')
@owner_required
def staff():
    conn = get_db()
    members = [dict(s) for s in conn.execute(
        "SELECT * FROM staff WHERE pharmacy_id=? ORDER BY created_at DESC",
        (session['pharmacy_id'],)).fetchall()]
    conn.close()
    return render_template('staff.html', staff=members)

@app.route('/staff/add', methods=['POST'])
@owner_required
def add_staff():
    conn = get_db()
    name = request.form['name']
    try:
        conn.execute("INSERT INTO staff (pharmacy_id,name,email,password,phone,role) VALUES (?,?,?,?,?,?)",
                     (session['pharmacy_id'], name,
                      request.form['email'].strip().lower(),
                      hashlib.sha256(request.form['password'].encode()).hexdigest(),
                      request.form.get('phone',''), request.form.get('role','staff')))
        log_action(session['pharmacy_id'], session['user_id'], session['role'],
                   'STAFF_ADDED', f"New staff: {name}", conn=conn)
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()
    return redirect(url_for('staff'))

@app.route('/staff/toggle/<int:staff_id>', methods=['POST'])
@owner_required
def toggle_staff(staff_id):
    conn = get_db()
    s = conn.execute("SELECT * FROM staff WHERE id=? AND pharmacy_id=?",
                     (staff_id, session['pharmacy_id'])).fetchone()
    if s:
        new_status = 0 if s['is_active'] else 1
        conn.execute("UPDATE staff SET is_active=? WHERE id=?", (new_status, staff_id))
        log_action(session['pharmacy_id'], session['user_id'], session['role'],
                   'STAFF_STATUS_CHANGED',
                   f"{s['name']} {'activated' if new_status else 'deactivated'}", conn=conn)
        conn.commit()
    conn.close()
    return redirect(url_for('staff'))

# ── Audit ─────────────────────────────────────────────────────
@app.route('/audit')
@owner_required
def audit():
    conn = get_db()
    logs = [dict(l) for l in conn.execute(
        "SELECT * FROM audit_logs WHERE pharmacy_id=? ORDER BY created_at DESC LIMIT 100",
        (session['pharmacy_id'],)).fetchall()]
    conn.close()
    return render_template('audit.html', logs=logs)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
