import os
import sqlite3
from datetime import datetime, timedelta
import random

DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db():
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    else:
        DB_PATH = os.path.join(os.path.dirname(__file__), 'clinixis.db')
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS pharmacies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            owner TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            phone TEXT,
            whatsapp TEXT,
            business_type TEXT DEFAULT 'pharmacy',
            address TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pharmacy_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            phone TEXT,
            role TEXT NOT NULL DEFAULT 'staff',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (pharmacy_id) REFERENCES pharmacies(id)
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pharmacy_id INTEGER NOT NULL,
            user_id INTEGER,
            user_type TEXT DEFAULT 'owner',
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (pharmacy_id) REFERENCES pharmacies(id)
        );

        -- Parent: static product definition
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pharmacy_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT DEFAULT 'General',
            unit TEXT DEFAULT 'piece',
            selling_price REAL DEFAULT 0,
            low_stock_alert INTEGER DEFAULT 10,
            supplier TEXT,
            barcode TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (pharmacy_id) REFERENCES pharmacies(id)
        );

        -- Child: dynamic batch shipments (FIFO)
        CREATE TABLE IF NOT EXISTS product_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            pharmacy_id INTEGER NOT NULL,
            batch_number TEXT,
            cost_price REAL NOT NULL DEFAULT 0,
            quantity_received INTEGER NOT NULL DEFAULT 0,
            quantity_remaining INTEGER NOT NULL DEFAULT 0,
            expiry_date TEXT,
            date_received TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (pharmacy_id) REFERENCES pharmacies(id)
        );

        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pharmacy_id INTEGER NOT NULL,
            staff_id INTEGER,
            total_amount REAL NOT NULL,
            note TEXT,
            voided INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (pharmacy_id) REFERENCES pharmacies(id)
        );

        CREATE TABLE IF NOT EXISTS sale_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            subtotal REAL NOT NULL,
            FOREIGN KEY (sale_id) REFERENCES sales(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        );

        -- Tracks which batches were consumed per sale line (for FIFO audit)
        CREATE TABLE IF NOT EXISTS sale_batch_deductions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_item_id INTEGER NOT NULL,
            batch_id INTEGER NOT NULL,
            quantity_deducted INTEGER NOT NULL,
            FOREIGN KEY (sale_item_id) REFERENCES sale_items(id),
            FOREIGN KEY (batch_id) REFERENCES product_batches(id)
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pharmacy_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (pharmacy_id) REFERENCES pharmacies(id)
        );

        CREATE TABLE IF NOT EXISTS stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pharmacy_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            batch_id INTEGER,
            movement_type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (pharmacy_id) REFERENCES pharmacies(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        );
    ''')

    # ── Safe migrations for older databases ──────────────────
    migrations = [
        "ALTER TABLE sales ADD COLUMN voided INTEGER DEFAULT 0",
        "ALTER TABLE sales ADD COLUMN staff_id INTEGER",
        "ALTER TABLE pharmacies ADD COLUMN whatsapp TEXT",
        "ALTER TABLE pharmacies ADD COLUMN business_type TEXT DEFAULT 'pharmacy'",
        "ALTER TABLE pharmacies ADD COLUMN address TEXT",
        "ALTER TABLE products ADD COLUMN category TEXT DEFAULT 'General'",
        "ALTER TABLE products ADD COLUMN supplier TEXT",
        "ALTER TABLE products ADD COLUMN barcode TEXT",
    ]
    for migration in migrations:
        try:
            c.execute(migration)
        except Exception:
            pass  # Column already exists — skip
    conn.commit()

    # Seed demo data
    c.execute("SELECT id FROM pharmacies WHERE email='demo@clinixis.ng'")
    if not c.fetchone():
        import hashlib
        pw = hashlib.sha256('demo1234'.encode()).hexdigest()
        c.execute("""INSERT INTO pharmacies (name,owner,email,password,phone,whatsapp,business_type)
                     VALUES (?,?,?,?,?,?,?)""",
                  ('Grace Pharmacy','Grace Okoro','demo@clinixis.ng',pw,
                   '08012345678','2348012345678','pharmacy'))
        pid = c.lastrowid

        staff_pw = hashlib.sha256('staff1234'.encode()).hexdigest()
        c.execute("INSERT INTO staff (pharmacy_id,name,email,password,phone,role) VALUES (?,?,?,?,?,?)",
                  (pid,'Emeka Obi','staff@clinixis.ng',staff_pw,'08087654321','staff'))

        today = datetime.now()

        products_data = [
            ('Paracetamol 500mg', 'Analgesic', 'pack', 250, 10, 'Emzor Pharma'),
            ('Amoxicillin 250mg', 'Antibiotic', 'pack', 650, 5,  'GSK Nigeria'),
            ('Vitamin C 1000mg',  'Supplement', 'bottle', 1200, 8, 'Wellcare'),
            ('ORS Sachets',       'Hydration',  'sachet', 100, 15, 'Local Supplier'),
            ('Ibuprofen 400mg',   'Analgesic',  'pack', 350, 5,  'Emzor Pharma'),
            ('Coartem 6-pack',    'Antimalarial','pack', 1800, 5, 'Novartis'),
            ('BP Monitor',        'Equipment',  'unit', 12000, 2, 'MedEquip Ltd'),
            ('Glucometer Kit',    'Equipment',  'unit', 9500, 2,  'Accu-Check Nigeria'),
        ]

        # Batch data: (cost_price, qty, expiry_offset_days, batch_suffix)
        batch_configs = [
            [(150, 20, 180, 'A'), (160, 25, 270, 'B')],   # Paracetamol — 2 batches
            [(400, 10, 60,  'A'), (420, 12, 120, 'B')],   # Amoxicillin
            [(800, 15, 365, 'A'), (820, 15, 400, 'B')],   # Vitamin C
            [(50,  5,  20,  'A'), (55,  3,  60,  'B')],   # ORS — first batch nearly expired!
            [(200, 10, 240, 'A'), (210, 8,  300, 'B')],   # Ibuprofen
            [(1200,8,  300, 'A'), (1250,4,  360, 'B')],   # Coartem
            [(8500, 3, None, 'A')],                         # BP Monitor — no expiry
            [(6000, 3, None, 'A')],                         # Glucometer — no expiry
        ]

        for i, (name, cat, unit, sell, alert, supplier) in enumerate(products_data):
            c.execute("""INSERT INTO products (pharmacy_id,name,category,unit,selling_price,low_stock_alert,supplier)
                         VALUES (?,?,?,?,?,?,?)""", (pid, name, cat, unit, sell, alert, supplier))
            prod_id = c.lastrowid

            for j, batch in enumerate(batch_configs[i]):
                cost, qty, exp_offset, suffix = batch
                exp_date = (today + timedelta(days=exp_offset)).strftime('%Y-%m-%d') if exp_offset else None
                batch_num = f'BCH-{prod_id:03d}-{suffix}'
                c.execute("""INSERT INTO product_batches
                    (product_id,pharmacy_id,batch_number,cost_price,quantity_received,quantity_remaining,expiry_date,date_received)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (prod_id, pid, batch_num, cost, qty, qty, exp_date,
                     (today - timedelta(days=30 if j==0 else 5)).strftime('%Y-%m-%d %H:%M:%S')))

        # Demo sales
        for days_ago in range(13, -1, -1):
            dt = (today - timedelta(days=days_ago)).strftime('%Y-%m-%d %H:%M:%S')
            for _ in range(random.randint(2, 4)):
                total = random.choice([1250, 2500, 650, 1800, 3200, 500, 900])
                c.execute("INSERT INTO sales (pharmacy_id,total_amount,created_at,voided) VALUES (?,?,?,0)",
                          (pid, total, dt))

        for cat, desc, amt in [('Rent','Monthly shop rent',45000),('Electricity','NEPA bill',8500),
                                ('Transport','Restock trip',3000),('Miscellaneous','Office supplies',1500)]:
            c.execute("INSERT INTO expenses (pharmacy_id,category,description,amount) VALUES (?,?,?,?)",
                      (pid, cat, desc, amt))

        c.execute("INSERT INTO audit_logs (pharmacy_id,user_id,user_type,action,details) VALUES (?,?,?,?,?)",
                  (pid, pid, 'owner', 'ACCOUNT_CREATED', 'Demo account initialized with FIFO batch tracking'))

    conn.commit()
    conn.close()


# ── FIFO deduction engine ─────────────────────────────────────
def fifo_deduct(conn, product_id, pharmacy_id, qty_needed):
    """
    Deducts qty_needed from product batches using FIFO (earliest expiry first).
    Returns list of (batch_id, qty_deducted) tuples.
    Raises ValueError if insufficient stock.
    """
    today = datetime.now().strftime('%Y-%m-%d')

    # Get active batches: unexpired first (soonest expiry), then no-expiry batches
    batches = conn.execute("""
        SELECT id, batch_number, quantity_remaining, expiry_date, cost_price
        FROM product_batches
        WHERE product_id=? AND pharmacy_id=? AND quantity_remaining>0
          AND (expiry_date IS NULL OR expiry_date >= ?)
        ORDER BY
            CASE WHEN expiry_date IS NULL THEN 1 ELSE 0 END,
            expiry_date ASC
    """, (product_id, pharmacy_id, today)).fetchall()

    total_available = sum(b['quantity_remaining'] for b in batches)
    if total_available < qty_needed:
        raise ValueError(f"Only {total_available} units available, {qty_needed} requested")

    deductions = []
    remaining = qty_needed

    for batch in batches:
        if remaining <= 0:
            break
        take = min(remaining, batch['quantity_remaining'])
        new_qty = batch['quantity_remaining'] - take
        conn.execute("UPDATE product_batches SET quantity_remaining=? WHERE id=?",
                     (new_qty, batch['id']))
        deductions.append((batch['id'], take))
        remaining -= take

    return deductions


def get_product_stock(conn, product_id, pharmacy_id):
    """Returns total available (unexpired) stock for a product."""
    today = datetime.now().strftime('%Y-%m-%d')
    result = conn.execute("""
        SELECT COALESCE(SUM(quantity_remaining), 0) as total
        FROM product_batches
        WHERE product_id=? AND pharmacy_id=? AND quantity_remaining>0
          AND (expiry_date IS NULL OR expiry_date >= ?)
    """, (product_id, pharmacy_id, today)).fetchone()
    return result['total'] if result else 0


def get_all_products_with_stock(pharmacy_id):
    """Returns products with computed stock totals from batches."""
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    products = conn.execute(
        "SELECT * FROM products WHERE pharmacy_id=? ORDER BY name", (pharmacy_id,)).fetchall()
    result = []
    for p in products:
        stock = get_product_stock(conn, p['id'], pharmacy_id)
        batches = conn.execute("""
            SELECT * FROM product_batches
            WHERE product_id=? AND pharmacy_id=?
            ORDER BY CASE WHEN expiry_date IS NULL THEN 1 ELSE 0 END, expiry_date ASC
        """, (p['id'], pharmacy_id)).fetchall()
        d = dict(p)
        d['quantity'] = stock
        d['batches'] = [dict(b) for b in batches]

        # Nearest expiry from active batches
        active_exp = [b['expiry_date'] for b in batches
                      if b['expiry_date'] and b['quantity_remaining'] > 0]
        d['nearest_expiry'] = min(active_exp) if active_exp else None

        # Low stock flag
        d['is_low'] = stock <= p['low_stock_alert']
        result.append(d)
    conn.close()
    return result


def get_dashboard_data(pharmacy_id):
    conn = get_db()
    c = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    soon = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')

    c.execute("SELECT COALESCE(SUM(total_amount),0) as t FROM sales WHERE pharmacy_id=? AND DATE(created_at)=? AND voided=0", (pharmacy_id, today))
    today_sales = c.fetchone()['t']
    c.execute("SELECT COUNT(*) as n FROM sales WHERE pharmacy_id=? AND DATE(created_at)=? AND voided=0", (pharmacy_id, today))
    today_tx = c.fetchone()['n']
    c.execute("SELECT COALESCE(SUM(total_amount),0) as t FROM sales WHERE pharmacy_id=? AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now') AND voided=0", (pharmacy_id,))
    month_sales = c.fetchone()['t']
    c.execute("SELECT COALESCE(SUM(amount),0) as t FROM expenses WHERE pharmacy_id=? AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now')", (pharmacy_id,))
    month_expenses = c.fetchone()['t']

    # Low stock: count products where total batch stock <= alert
    products = get_all_products_with_stock(pharmacy_id)
    low_stock_items = [p for p in products if p['is_low'] and p['quantity'] > 0]
    out_of_stock = [p for p in products if p['quantity'] == 0]
    low_stock_count = len(low_stock_items) + len(out_of_stock)

    # Expiring batches
    c.execute("""SELECT pb.*, p.name as product_name FROM product_batches pb
                 JOIN products p ON pb.product_id=p.id
                 WHERE pb.pharmacy_id=? AND pb.expiry_date<=? AND pb.expiry_date>=?
                   AND pb.quantity_remaining>0
                 ORDER BY pb.expiry_date ASC LIMIT 5""", (pharmacy_id, soon, today))
    expiring = [dict(r) for r in c.fetchall()]
    expiring_count = len(expiring)

    c.execute("SELECT DATE(created_at) as day,SUM(total_amount) as total FROM sales WHERE pharmacy_id=? AND voided=0 AND created_at>=date('now','-13 days') GROUP BY DATE(created_at) ORDER BY day", (pharmacy_id,))
    chart_data = [dict(r) for r in c.fetchall()]

    c.execute("""SELECT p.name,SUM(si.quantity) as units,SUM(si.subtotal) as revenue
                 FROM sale_items si JOIN products p ON si.product_id=p.id
                 WHERE p.pharmacy_id=? GROUP BY p.id ORDER BY revenue DESC LIMIT 5""", (pharmacy_id,))
    top_products = [dict(r) for r in c.fetchall()]

    conn.close()
    return {
        'today_sales': today_sales, 'today_transactions': today_tx,
        'month_sales': month_sales, 'month_expenses': month_expenses,
        'month_profit': month_sales - month_expenses,
        'low_stock_count': low_stock_count, 'expiring_count': expiring_count,
        'chart_data': chart_data, 'top_products': top_products,
        'low_stock': low_stock_items[:5], 'expiring': expiring,
    }


def get_expiry_digest(pharmacy_id):
    """
    Generates the expiry digest data for the weekly email.
    Returns list of expiring batch dicts with financial risk exposure.
    """
    conn = get_db()
    soon = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
    today = datetime.now().strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT pb.*, p.name as product_name, p.selling_price, p.category
        FROM product_batches pb
        JOIN products p ON pb.product_id=p.id
        WHERE pb.pharmacy_id=? AND pb.expiry_date<=? AND pb.expiry_date>=?
          AND pb.quantity_remaining>0
        ORDER BY pb.expiry_date ASC
    """, (pharmacy_id, soon, today)).fetchall()
    pharmacy = conn.execute("SELECT * FROM pharmacies WHERE id=?", (pharmacy_id,)).fetchone()
    conn.close()

    items = []
    total_risk = 0
    for r in rows:
        risk = r['quantity_remaining'] * r['cost_price']
        total_risk += risk
        days_left = (datetime.strptime(r['expiry_date'], '%Y-%m-%d') - datetime.now()).days
        items.append({
            **dict(r),
            'risk_exposure': risk,
            'days_left': days_left,
        })
    return {'items': items, 'total_risk': total_risk, 'pharmacy': dict(pharmacy) if pharmacy else {}}


def log_action(pharmacy_id, user_id, user_type, action, details='', conn=None):
    """Write audit log. Pass existing conn to avoid opening a second connection."""
    close_after = False
    if conn is None:
        conn = get_db()
        close_after = True
    conn.execute("INSERT INTO audit_logs (pharmacy_id,user_id,user_type,action,details) VALUES (?,?,?,?,?)",
                 (pharmacy_id, user_id, user_type, action, details))
    if close_after:
        conn.commit()
        conn.close()


if __name__ == '__main__':
    init_db()
    print("Clinixis database ready with FIFO batch tracking.")


def get_sales_velocity(pharmacy_id):
    """
    Calculates daily burn rate for each product over last 30 days.
    Returns list of products with velocity data and days_until_stockout.
    """
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')

    # Get all products with current stock
    products = get_all_products_with_stock(pharmacy_id)

    # Get sales in last 30 days per product
    sales_data = conn.execute("""
        SELECT si.product_id, SUM(si.quantity) as total_sold
        FROM sale_items si
        JOIN sales s ON si.sale_id = s.id
        WHERE s.pharmacy_id = ? AND s.voided = 0
          AND s.created_at >= date('now', '-30 days')
        GROUP BY si.product_id
    """, (pharmacy_id,)).fetchall()
    conn.close()

    velocity_map = {r['product_id']: r['total_sold'] for r in sales_data}

    result = []
    for p in products:
        total_sold = velocity_map.get(p['id'], 0)
        daily_rate = round(total_sold / 30, 2)
        if daily_rate > 0 and p['quantity'] > 0:
            days_left = round(p['quantity'] / daily_rate)
        elif p['quantity'] == 0:
            days_left = 0
        else:
            days_left = None  # No sales data

        # Profit margin from first batch cost
        margin = None
        if p['batches']:
            first_cost = p['batches'][0].get('cost_price', 0)
            if first_cost > 0 and p['selling_price'] > 0:
                margin = round(((p['selling_price'] - first_cost) / p['selling_price']) * 100, 1)

        result.append({
            **p,
            'daily_rate': daily_rate,
            'total_sold_30d': total_sold,
            'days_until_stockout': days_left if days_left is not None else 9999,
            'has_velocity': days_left is not None,
            'margin': margin,
            'needs_restock': days_left is not None and days_left <= 7,
            'low_margin': margin is not None and margin < 20,
        })

    # Sort: critical first (low days), then by revenue
    result.sort(key=lambda x: (
        x['days_until_stockout'] if x['days_until_stockout'] is not None else 9999
    ))
    return result


def get_smart_insights(pharmacy_id):
    """Generate actionable insights for the dashboard."""
    conn = get_db()

    # Best selling day of week
    best_day = conn.execute("""
        SELECT strftime('%w', created_at) as dow,
               SUM(total_amount) as revenue,
               COUNT(*) as tx
        FROM sales WHERE pharmacy_id=? AND voided=0
          AND created_at >= date('now', '-60 days')
        GROUP BY dow ORDER BY revenue DESC LIMIT 1
    """, (pharmacy_id,)).fetchone()

    # Today vs yesterday
    today_rev = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) as t FROM sales WHERE pharmacy_id=? AND DATE(created_at)=DATE('now') AND voided=0",
        (pharmacy_id,)).fetchone()['t']
    yesterday_rev = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) as t FROM sales WHERE pharmacy_id=? AND DATE(created_at)=DATE('now','-1 day') AND voided=0",
        (pharmacy_id,)).fetchone()['t']

    # This week vs last week
    this_week = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) as t FROM sales WHERE pharmacy_id=? AND created_at>=date('now','-7 days') AND voided=0",
        (pharmacy_id,)).fetchone()['t']
    last_week = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) as t FROM sales WHERE pharmacy_id=? AND created_at>=date('now','-14 days') AND created_at<date('now','-7 days') AND voided=0",
        (pharmacy_id,)).fetchone()['t']

    # Average transaction value
    avg_tx = conn.execute(
        "SELECT COALESCE(AVG(total_amount),0) as t FROM sales WHERE pharmacy_id=? AND voided=0 AND created_at>=date('now','-30 days')",
        (pharmacy_id,)).fetchone()['t']

    conn.close()

    days = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']
    week_change = ((this_week - last_week) / last_week * 100) if last_week > 0 else 0
    day_change = ((today_rev - yesterday_rev) / yesterday_rev * 100) if yesterday_rev > 0 else 0

    return {
        'best_day': days[int(best_day['dow'])] if best_day else None,
        'today_rev': today_rev,
        'yesterday_rev': yesterday_rev,
        'day_change': round(day_change, 1),
        'this_week': this_week,
        'last_week': last_week,
        'week_change': round(week_change, 1),
        'avg_tx': round(avg_tx, 0),
    }


def get_recent_activity(pharmacy_id, limit=20):
    """Returns unified chronological activity feed."""
    conn = get_db()

    sales = conn.execute("""
        SELECT 'sale' as type, s.id, s.total_amount as amount,
               s.created_at, s.note,
               COUNT(si.id) as item_count,
               COALESCE(st.name, p.owner) as actor
        FROM sales s
        LEFT JOIN sale_items si ON s.id=si.sale_id
        LEFT JOIN staff st ON s.staff_id=st.id
        LEFT JOIN pharmacies p ON s.pharmacy_id=p.id
        WHERE s.pharmacy_id=? AND s.voided=0
        GROUP BY s.id ORDER BY s.created_at DESC LIMIT 10
    """, (pharmacy_id,)).fetchall()

    expenses = conn.execute("""
        SELECT 'expense' as type, id, amount, created_at,
               description as note, category, NULL as item_count, NULL as actor
        FROM expenses WHERE pharmacy_id=?
        ORDER BY created_at DESC LIMIT 5
    """, (pharmacy_id,)).fetchall()

    batches = conn.execute("""
        SELECT 'restock' as type, pb.id, pb.quantity_received as amount,
               pb.date_received as created_at,
               p.name as note, pb.batch_number, NULL as item_count, NULL as actor
        FROM product_batches pb
        JOIN products p ON pb.product_id=p.id
        WHERE pb.pharmacy_id=?
        ORDER BY pb.date_received DESC LIMIT 5
    """, (pharmacy_id,)).fetchall()

    all_activity = [dict(r) for r in list(sales) + list(expenses) + list(batches)]
    all_activity.sort(key=lambda x: x['created_at'], reverse=True)
    conn.close()
    return all_activity[:limit]


def get_sale_receipt(sale_id, pharmacy_id):
    """Returns full receipt data for a sale."""
    conn = get_db()
    sale = conn.execute("""
        SELECT s.*, p.name as pharmacy_name, p.address, p.phone,
               COALESCE(st.name, ph.owner) as served_by
        FROM sales s
        JOIN pharmacies ph ON s.pharmacy_id=ph.id
        LEFT JOIN staff st ON s.staff_id=st.id
        LEFT JOIN pharmacies p ON s.pharmacy_id=p.id
        WHERE s.id=? AND s.pharmacy_id=?
    """, (sale_id, pharmacy_id)).fetchone()

    if not sale:
        conn.close()
        return None

    items = conn.execute("""
        SELECT si.*, p.name as product_name, p.unit, p.category
        FROM sale_items si
        JOIN products p ON si.product_id=p.id
        WHERE si.sale_id=?
    """, (sale_id,)).fetchall()

    conn.close()
    return {
        'sale': dict(sale),
        'items': [dict(i) for i in items]
    }


HEALTH_TIPS = [
    {"icon": "fa-droplet", "color": "#1565c0", "tip": "Did you know? Drinking 8 glasses of water daily helps flush toxins, improve skin, and boost energy levels significantly."},
    {"icon": "fa-heart-pulse", "color": "#c62828", "tip": "The heart beats about 100,000 times per day — pumping roughly 2,000 gallons of blood through your body."},
    {"icon": "fa-lungs", "color": "#00796b", "tip": "Deep breathing for 5 minutes can reduce cortisol levels and lower blood pressure almost immediately."},
    {"icon": "fa-moon", "color": "#5c35a0", "tip": "Health practitioners say adults need 7–9 hours of sleep. Poor sleep is linked to heart disease, diabetes, and obesity."},
    {"icon": "fa-bowl-food", "color": "#e65100", "tip": "Eating at least 5 portions of fruit and vegetables daily reduces the risk of serious health conditions by up to 30%."},
    {"icon": "fa-person-walking", "color": "#2e7d32", "tip": "Walking just 30 minutes daily can lower the risk of heart disease, strengthen bones, and improve mental health."},
    {"icon": "fa-hand-sparkles", "color": "#1565c0", "tip": "Proper handwashing for 20 seconds removes 99% of bacteria and is the single most effective way to prevent infections."},
    {"icon": "fa-sun", "color": "#f57f17", "tip": "10–15 minutes of morning sunlight daily boosts Vitamin D, improves mood, and regulates your sleep cycle."},
    {"icon": "fa-brain", "color": "#6a1b9a", "tip": "The human brain uses about 20% of the body's total energy despite being only 2% of total body weight."},
    {"icon": "fa-apple-whole", "color": "#2e7d32", "tip": "An apple a day really does help — apples contain quercetin, catechin, and chlorogenic acid that protect heart health."},
    {"icon": "fa-shield-heart", "color": "#c62828", "tip": "Regular blood pressure checks save lives. Hypertension is called the 'silent killer' because it has no visible symptoms."},
    {"icon": "fa-syringe", "color": "#00796b", "tip": "Vaccination prevents 2–3 million deaths worldwide every year. Routine immunisation is one of the most cost-effective health interventions."},
    {"icon": "fa-dumbbell", "color": "#1565c0", "tip": "Even light exercise 3× per week reduces depression symptoms by 47% — as effective as antidepressants for mild cases."},
    {"icon": "fa-tooth", "color": "#0277bd", "tip": "Oral health is linked to overall health. Gum disease increases the risk of heart disease, diabetes, and premature birth."},
    {"icon": "fa-eye", "color": "#00796b", "tip": "The 20-20-20 rule: every 20 minutes, look at something 20 feet away for 20 seconds to reduce digital eye strain."},
]


def seed_hospital_products(pharmacy_id):
    """Seeds a comprehensive list of hospital/pharmacy products."""
    conn = get_db()
    existing = conn.execute("SELECT COUNT(*) as n FROM products WHERE pharmacy_id=?", (pharmacy_id,)).fetchone()['n']
    if existing >= 15:
        conn.close()
        return  # Already seeded

    today = datetime.now()
    hospital_products = [
        # Medications
        ('Metronidazole 400mg', 'Antibiotic', 'pack', 180, 300, 20, 5, (today+timedelta(days=240)).strftime('%Y-%m-%d'), 'May & Baker', 150, 25),
        ('Ciprofloxacin 500mg', 'Antibiotic', 'pack', 350, 600, 15, 5, (today+timedelta(days=180)).strftime('%Y-%m-%d'), 'Fidson', 280, 18),
        ('Artemether/Lumefantrine', 'Antimalarial', 'pack', 900, 1400, 20, 5, (today+timedelta(days=300)).strftime('%Y-%m-%d'), 'Novartis', 720, 24),
        ('Insulin (Actrapid)', 'Hormone', 'vial', 1800, 2800, 10, 3, (today+timedelta(days=90)).strftime('%Y-%m-%d'), 'Novo Nordisk', 1500, 12),
        ('Amlodipine 5mg', 'Antihypertensive', 'pack', 120, 220, 30, 10, (today+timedelta(days=365)).strftime('%Y-%m-%d'), 'Emzor Pharma', 100, 35),
        ('Lisinopril 10mg', 'Antihypertensive', 'pack', 200, 380, 25, 8, (today+timedelta(days=400)).strftime('%Y-%m-%d'), 'GSK Nigeria', 160, 28),
        ('Metformin 500mg', 'Antidiabetic', 'pack', 150, 280, 20, 8, (today+timedelta(days=360)).strftime('%Y-%m-%d'), 'Fidson', 120, 22),
        ('Omeprazole 20mg', 'Antacid', 'pack', 250, 420, 18, 6, (today+timedelta(days=300)).strftime('%Y-%m-%d'), 'May & Baker', 200, 20),
        ('Diclofenac 50mg', 'NSAID', 'pack', 130, 230, 22, 8, (today+timedelta(days=280)).strftime('%Y-%m-%d'), 'Emzor Pharma', 100, 25),
        ('Amoxicillin + Clavulanate', 'Antibiotic', 'pack', 800, 1300, 12, 4, (today+timedelta(days=150)).strftime('%Y-%m-%d'), 'GSK Nigeria', 640, 15),
        ('Dexamethasone 4mg', 'Steroid', 'vial', 400, 700, 15, 5, (today+timedelta(days=200)).strftime('%Y-%m-%d'), 'Emzor Pharma', 320, 18),
        ('Tramadol 50mg', 'Analgesic', 'pack', 300, 520, 18, 5, (today+timedelta(days=360)).strftime('%Y-%m-%d'), 'Fidson', 240, 20),
        ('Folic Acid 5mg', 'Supplement', 'pack', 80, 150, 40, 10, (today+timedelta(days=365)).strftime('%Y-%m-%d'), 'Local Supplier', 60, 45),
        ('Ferrous Sulphate', 'Supplement', 'pack', 90, 170, 35, 10, (today+timedelta(days=365)).strftime('%Y-%m-%d'), 'Local Supplier', 70, 38),
        ('Chlorpheniramine 4mg', 'Antihistamine', 'pack', 70, 130, 30, 8, (today+timedelta(days=300)).strftime('%Y-%m-%d'), 'May & Baker', 55, 32),
        ('IV Fluid NS 500ml', 'IV Fluid', 'bag', 350, 600, 20, 5, (today+timedelta(days=180)).strftime('%Y-%m-%d'), 'Unique Pharma', 280, 22),
        ('IV Fluid D5W 500ml', 'IV Fluid', 'bag', 380, 650, 18, 5, (today+timedelta(days=180)).strftime('%Y-%m-%d'), 'Unique Pharma', 300, 20),
        ('Ringer\'s Lactate 500ml', 'IV Fluid', 'bag', 400, 700, 15, 5, (today+timedelta(days=180)).strftime('%Y-%m-%d'), 'Unique Pharma', 320, 18),
        # Consumables
        ('Disposable Syringe 5ml', 'Consumable', 'piece', 30, 60, 100, 20, None, 'Mopson', 25, 120),
        ('Disposable Syringe 10ml', 'Consumable', 'piece', 40, 80, 80, 20, None, 'Mopson', 32, 95),
        ('IV Cannula 18G', 'Consumable', 'piece', 120, 220, 50, 10, None, 'Mopson', 95, 60),
        ('IV Cannula 20G', 'Consumable', 'piece', 110, 200, 60, 10, None, 'Mopson', 88, 72),
        ('Surgical Gloves (M)', 'Consumable', 'pair', 80, 150, 80, 20, None, 'Surgikos', 65, 90),
        ('Surgical Gloves (L)', 'Consumable', 'pair', 80, 150, 70, 20, None, 'Surgikos', 65, 85),
        ('Examination Gloves (Box)', 'Consumable', 'box', 1800, 3000, 10, 3, None, 'Surgikos', 1440, 12),
        ('Bandage Roll 10cm', 'Consumable', 'piece', 150, 280, 30, 8, None, 'Local Supplier', 120, 35),
        ('Cotton Wool 100g', 'Consumable', 'piece', 200, 350, 25, 6, None, 'Local Supplier', 160, 28),
        ('Plaster Strip (Box)', 'Consumable', 'box', 300, 520, 20, 5, None, 'Local Supplier', 240, 22),
        ('Urine Dipstick (10 param)', 'Diagnostic', 'pack', 1200, 2000, 8, 2, (today+timedelta(days=180)).strftime('%Y-%m-%d'), 'Roche', 960, 10),
        ('Pregnancy Test Strip', 'Diagnostic', 'piece', 80, 200, 50, 10, (today+timedelta(days=365)).strftime('%Y-%m-%d'), 'MedLine', 65, 60),
        ('Malaria RDT Kit', 'Diagnostic', 'piece', 350, 600, 30, 8, (today+timedelta(days=180)).strftime('%Y-%m-%d'), 'SD Bioline', 280, 35),
        ('Blood Glucose Strip (50)', 'Diagnostic', 'pack', 1500, 2500, 10, 3, (today+timedelta(days=180)).strftime('%Y-%m-%d'), 'Accu-Check', 1200, 12),
        # Equipment
        ('Digital Thermometer', 'Equipment', 'unit', 1200, 2000, 8, 2, None, 'MedEquip Ltd', 960, 10),
        ('Pulse Oximeter', 'Equipment', 'unit', 4500, 7500, 5, 2, None, 'MedEquip Ltd', 3600, 6),
        ('Stethoscope (Adult)', 'Equipment', 'unit', 5500, 9000, 4, 1, None, 'Littmann NG', 4400, 5),
        ('Nebulizer Machine', 'Equipment', 'unit', 15000, 25000, 3, 1, None, 'MedEquip Ltd', 12000, 4),
        ('Weighing Scale (Baby)', 'Equipment', 'unit', 12000, 20000, 2, 1, None, 'MedEquip Ltd', 9600, 3),
        ('Otoscope', 'Equipment', 'unit', 8000, 14000, 2, 1, None, 'MedEquip Ltd', 6400, 3),
        ('Surgical Scissors', 'Equipment', 'unit', 1500, 2800, 5, 2, None, 'Surgikos', 1200, 6),
        ('Forceps (Dressing)', 'Equipment', 'unit', 1200, 2200, 5, 2, None, 'Surgikos', 960, 6),
    ]

    for prod in hospital_products:
        name, cat, unit, cost, sell, qty, alert, exp, supplier, batch_cost, batch_qty = prod
        # Check if product already exists
        existing_prod = conn.execute(
            "SELECT id FROM products WHERE pharmacy_id=? AND name=?", (pharmacy_id, name)).fetchone()
        if existing_prod:
            continue
        conn.execute("""INSERT INTO products (pharmacy_id,name,category,unit,selling_price,low_stock_alert,supplier)
                        VALUES (?,?,?,?,?,?,?)""",
                     (pharmacy_id, name, cat, unit, sell, alert, supplier))
        prod_id = conn.execute("SELECT last_insert_rowid() as id").fetchone()['id']
        if qty > 0:
            conn.execute("""INSERT INTO product_batches
                (product_id,pharmacy_id,batch_number,cost_price,quantity_received,quantity_remaining,expiry_date)
                VALUES (?,?,?,?,?,?,?)""",
                (prod_id, pharmacy_id, f"BCH-{prod_id:03d}-A", batch_cost, batch_qty, qty, exp))

    conn.commit()
    conn.close()
