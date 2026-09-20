
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
import sqlite3, os, csv, io, shutil, re, json
try:
    import cv2
    import numpy as np
except Exception:
    cv2 = None
    np = None
try:
    from pyzbar.pyzbar import decode as pyzbar_decode
except Exception:
    pyzbar_decode = None
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.graphics import renderSVG
import qrcode

BASE=os.path.dirname(os.path.abspath(__file__))
DB=os.path.join(BASE,"patel_pos.db")
app=Flask(__name__)
app.secret_key=os.environ.get("PATEL_POS_SECRET","change-this-secret-key")

STATUSES=["Received","Approved","Repairing","Testing","Ready","Delivered","Cancelled"]
PAYMENTS=["Cash","UPI","Card","Bank Transfer","Other"]

def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init_db():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT NOT NULL,role TEXT DEFAULT 'staff');
    CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY,name TEXT NOT NULL,phone TEXT,address TEXT,email TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS jobs(
      id INTEGER PRIMARY KEY,job_no TEXT UNIQUE,customer_id INTEGER,appliance TEXT,brand TEXT,model TEXT,serial TEXT,
      complaint TEXT,accessories TEXT,condition TEXT,technician TEXT,status TEXT,
      discount REAL DEFAULT 0,photo TEXT,received_at TEXT,delivered_at TEXT,
      FOREIGN KEY(customer_id) REFERENCES customers(id)
    );
    CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY,job_id INTEGER,description TEXT,hsn TEXT,qty REAL,rate REAL,kind TEXT,inventory_id INTEGER,
      FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS inventory(id INTEGER PRIMARY KEY,sku TEXT UNIQUE,name TEXT,hsn TEXT,stock REAL DEFAULT 0,min_stock REAL DEFAULT 0,
      purchase REAL DEFAULT 0,sale REAL DEFAULT 0,unit TEXT DEFAULT 'pcs');
    CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY,job_id INTEGER,amount REAL,method TEXT,reference TEXT,paid_at TEXT,
      FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY,title TEXT,amount REAL,category TEXT,spent_at TEXT,note TEXT);
    CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY,name TEXT,phone TEXT,address TEXT,gst TEXT);
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,username TEXT,action TEXT,entity TEXT,entity_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS scan_events(id INTEGER PRIMARY KEY,job_id INTEGER,serial TEXT,scan_type TEXT DEFAULT 'allocation',scanned_by TEXT,scanned_at TEXT,scanned_date TEXT,device TEXT, FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS deleted_jobs(
      id INTEGER PRIMARY KEY, original_job_id INTEGER, job_no TEXT, serial TEXT,
      customer_name TEXT, phone TEXT, address TEXT, email TEXT, appliance TEXT, brand TEXT, model TEXT,
      complaint TEXT, accessories TEXT, condition TEXT, technician TEXT, status TEXT, discount REAL DEFAULT 0,
      received_at TEXT, delivered_at TEXT, deleted_at TEXT, deleted_by TEXT,
      subtotal REAL DEFAULT 0, total REAL DEFAULT 0, paid REAL DEFAULT 0, balance REAL DEFAULT 0,
      items_json TEXT, payments_json TEXT
    );
    CREATE TABLE IF NOT EXISTS serial_registry(id INTEGER PRIMARY KEY,serial TEXT UNIQUE NOT NULL,job_id INTEGER UNIQUE,created_at TEXT, FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE SET NULL);
    """)
    if not c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        c.execute("INSERT INTO users(username,password,role) VALUES(?,?,?)",("admin",generate_password_hash("admin"),"admin"))
    # Lightweight schema upgrades for existing databases
    user_cols={r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    if "must_change_password" not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 1")
    cols={r[1] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
    if "serial_status" not in cols: c.execute("ALTER TABLE jobs ADD COLUMN serial_status TEXT DEFAULT 'Active'")
    if "serial_created_at" not in cols: c.execute("ALTER TABLE jobs ADD COLUMN serial_created_at TEXT")
    if "last_scanned_at" not in cols: c.execute("ALTER TABLE jobs ADD COLUMN last_scanned_at TEXT")
    if "scan_count" not in cols: c.execute("ALTER TABLE jobs ADD COLUMN scan_count INTEGER DEFAULT 0")
    # Keep new and legacy records aligned where the serial can safely become the job number.
    c.execute("""UPDATE jobs SET job_no=serial
                 WHERE serial IS NOT NULL AND TRIM(serial)!=''
                 AND NOT EXISTS (SELECT 1 FROM jobs other WHERE other.job_no=jobs.serial AND other.id!=jobs.id)""")
    c.commit(); c.close()

def now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def log(action,entity="",entity_id=None):
    c=db(); c.execute("INSERT INTO audit(username,action,entity,entity_id,created_at) VALUES(?,?,?,?,?)",
                      (session.get("user","system"),action,entity,entity_id,now())); c.commit(); c.close()
def login_required(f):
    @wraps(f)
    def w(*a,**k):
        if "uid" not in session: return redirect(url_for("login",next=request.path))
        if session.get("must_change_password") and request.endpoint not in {"change_password", "logout"}:
            return redirect(url_for("change_password"))
        return f(*a,**k)
    return w

@app.context_processor
def inject():
    return {"STATUSES":STATUSES,"PAYMENTS":PAYMENTS,"app_name":"PATEL ELECTRICALS WORKSHOP"}

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        c=db(); u=c.execute("SELECT * FROM users WHERE username=?",(request.form["username"],)).fetchone(); c.close()
        if u and check_password_hash(u["password"],request.form["password"]):
            must_change=bool(u["must_change_password"]) if "must_change_password" in u.keys() else True
            session.update(uid=u["id"],user=u["username"],role=u["role"],must_change_password=must_change)
            if must_change:
                return redirect(url_for("change_password"))
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Invalid username or password","error")
    return render_template("login.html")

@app.route("/change-password",methods=["GET","POST"])
def change_password():
    if "uid" not in session:
        return redirect(url_for("login"))
    if request.method=="POST":
        current=request.form.get("current_password","")
        new_password=request.form.get("new_password","")
        confirm=request.form.get("confirm_password","")
        if len(new_password)<8:
            flash("New password must be at least 8 characters.","error")
        elif new_password!=confirm:
            flash("New passwords do not match.","error")
        else:
            c=db(); u=c.execute("SELECT password FROM users WHERE id=?",(session["uid"],)).fetchone()
            if not u or not check_password_hash(u["password"],current):
                c.close(); flash("Current password is incorrect.","error")
            else:
                c.execute("UPDATE users SET password=?, must_change_password=0 WHERE id=?",(generate_password_hash(new_password),session["uid"]))
                c.commit(); c.close()
                session["must_change_password"]=False
                flash("Password changed successfully.","ok")
                return redirect(url_for("dashboard"))
    return render_template("change_password.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

def job_totals(c, jid, discount=0):
    subtotal=c.execute("SELECT COALESCE(SUM(qty*rate),0) n FROM items WHERE job_id=?",(jid,)).fetchone()["n"]
    paid=c.execute("SELECT COALESCE(SUM(amount),0) n FROM payments WHERE job_id=?",(jid,)).fetchone()["n"]
    total=max(0, subtotal-(discount or 0))
    balance=max(0, total-paid)
    return subtotal, total, paid, balance

def total_pending_amount(c):
    rows=c.execute("SELECT j.id,j.discount FROM jobs j WHERE j.status!='Cancelled'").fetchall()
    return sum(job_totals(c,r["id"],r["discount"])[3] for r in rows)

def customer_current_balance(c, customer_id):
    if not customer_id:
        return 0.0
    rows=c.execute("SELECT id,discount FROM jobs WHERE customer_id=? AND status!='Cancelled'",(customer_id,)).fetchall()
    return sum(job_totals(c,r["id"],r["discount"])[3] for r in rows)

def report_job_rows(c, status=None, pending_only=False):
    query="""SELECT j.*, c.name customer, c.phone, c.address, c.email
              FROM jobs j LEFT JOIN customers c ON c.id=j.customer_id"""
    params=[]
    clauses=[]
    if status:
        clauses.append("j.status=?"); params.append(status)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY j.id DESC"
    rows=c.execute(query,params).fetchall()
    out=[]
    for r in rows:
        subtotal,total,paid,balance=job_totals(c,r["id"],r["discount"])
        if pending_only and balance <= 0:
            continue
        d=dict(r)
        d.update(subtotal=subtotal,total=total,paid=paid,balance=balance,customer_balance=customer_current_balance(c,r["customer_id"]))
        out.append(d)
    return out

@app.route("/")
@login_required
def dashboard():
    c=db()
    stats={
      "jobs":c.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"],
      "open":c.execute("SELECT COUNT(*) n FROM jobs WHERE status NOT IN ('Delivered','Cancelled')").fetchone()["n"],
      "ready":c.execute("SELECT COUNT(*) n FROM jobs WHERE status='Ready'").fetchone()["n"],
      "delivered":c.execute("SELECT COUNT(*) n FROM jobs WHERE status='Delivered'").fetchone()["n"],
      "deleted":c.execute("SELECT COUNT(*) n FROM deleted_jobs").fetchone()["n"],
      "low":c.execute("SELECT COUNT(*) n FROM inventory WHERE stock<=min_stock").fetchone()["n"],
      "sales":c.execute("SELECT COALESCE(SUM(amount),0) n FROM payments WHERE date(paid_at)=date('now')").fetchone()["n"]
    }
    stats["pending"] = total_pending_amount(c)
    jobs=c.execute("""SELECT j.*,c.name customer FROM jobs j LEFT JOIN customers c ON c.id=j.customer_id
                      ORDER BY j.id DESC LIMIT 30""").fetchall()
    c.close(); return render_template("dashboard.html",stats=stats,jobs=jobs)

@app.route("/deleted-jobs")
@login_required
def deleted_jobs():
    c=db(); raw=c.execute("SELECT * FROM deleted_jobs ORDER BY id DESC").fetchall(); c.close()
    rows=[]
    for row in raw:
        item=dict(row)
        try: item["item_count"]=len(json.loads(item.get("items_json") or "[]"))
        except Exception: item["item_count"]=0
        try: item["payment_count"]=len(json.loads(item.get("payments_json") or "[]"))
        except Exception: item["payment_count"]=0
        rows.append(item)
    return render_template("deleted_jobs.html", rows=rows)

@app.route("/customers",methods=["GET","POST"])
@login_required
def customers():
    c=db()
    if request.method=="POST":
        c.execute("INSERT INTO customers(name,phone,address,email,created_at) VALUES(?,?,?,?,?)",
                  (request.form["name"],request.form.get("phone"),request.form.get("address"),request.form.get("email"),now()))
        c.commit(); log("Created customer","customer",c.execute("SELECT last_insert_rowid()").fetchone()[0]); flash("Customer added","ok")
        return redirect(url_for("customers"))
    q=request.args.get("q","").strip()
    rows=c.execute("SELECT * FROM customers WHERE name LIKE ? OR phone LIKE ? ORDER BY id DESC",("%"+q+"%","%"+q+"%")).fetchall()
    c.close(); return render_template("customers.html",customers=rows,q=q)

def next_serial(c):
    row=c.execute("SELECT serial FROM serial_registry WHERE serial LIKE 'PE-S%' ORDER BY id DESC LIMIT 1").fetchone()
    n=1
    if row:
        m=re.search(r"(\d+)$", row["serial"] or "")
        if m: n=int(m.group(1))+1
    while c.execute("SELECT 1 FROM serial_registry WHERE serial=?",(f"PE-S{n:06d}",)).fetchone(): n+=1
    return f"PE-S{n:06d}"

@app.route("/jobs/new",methods=["GET","POST"])
@login_required
def new_job():
    c=db()
    customers=c.execute("SELECT * FROM customers ORDER BY name").fetchall()
    if request.method=="POST":
        customer_id=request.form.get("customer_id")
        if not customer_id:
            c.execute("INSERT INTO customers(name,phone,address,created_at) VALUES(?,?,?,?)",
                      (request.form.get("customer_name") or "Walk-in / Pending",request.form.get("phone"),request.form.get("address"),now()))
            customer_id=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        serial=(request.form.get("serial") or next_serial(c)).strip()
        job_no=serial
        c.execute("""INSERT INTO jobs(job_no,customer_id,appliance,brand,model,serial,complaint,technician,status,discount,received_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          (job_no,customer_id,request.form.get("appliance"),request.form.get("brand"),request.form.get("model"),
           serial,request.form.get("complaint"),request.form.get("technician"),"Received",0,now()))
        jid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        serial=c.execute("SELECT serial FROM jobs WHERE id=?",(jid,)).fetchone()["serial"]
        c.execute("INSERT OR IGNORE INTO serial_registry(serial,job_id,created_at) VALUES(?,?,?)",(serial,jid,now()))
        c.commit(); c.close(); log("Created job","job",jid)
        return redirect(url_for("job",jid=jid))
    c.close(); return render_template("job_form.html",customers=customers)

@app.route("/jobs/<int:jid>",methods=["GET","POST"])
@login_required
def job(jid):
    c=db(); j=c.execute("SELECT j.*,c.name customer,c.phone,c.address,c.email FROM jobs j LEFT JOIN customers c ON c.id=j.customer_id WHERE j.id=?",(jid,)).fetchone()
    if not j: c.close(); return "Not found",404
    if request.method=="POST":
        c.execute("""UPDATE jobs SET appliance=?,brand=?,model=?,serial=?,complaint=?,technician=?,status=?,discount=?,
                     delivered_at=CASE WHEN ?='Delivered' THEN COALESCE(delivered_at,?) WHEN ?!='Delivered' THEN NULL ELSE delivered_at END WHERE id=?""",
                  (request.form.get("appliance"),request.form.get("brand"),request.form.get("model"),request.form.get("serial"),request.form.get("complaint"),
                   request.form.get("technician"),request.form.get("status"),float(request.form.get("discount") or 0),
                   request.form.get("status"),now(),request.form.get("status"),jid))
        c.commit(); log("Updated job","job",jid); flash("Job updated","ok")
    items=c.execute("SELECT * FROM items WHERE job_id=? ORDER BY id",(jid,)).fetchall()
    pays=c.execute("SELECT * FROM payments WHERE job_id=? ORDER BY id DESC",(jid,)).fetchall()
    subtotal=sum((x["qty"] or 0)*(x["rate"] or 0) for x in items)
    total=max(0,subtotal-(j["discount"] or 0))
    paid=sum(x["amount"] for x in pays); balance=max(0,total-paid)
    c.close(); return render_template("job.html",j=j,items=items,pays=pays,subtotal=subtotal,total=total,paid=paid,balance=balance)


@app.route("/jobs/<int:jid>/delete", methods=["POST"])
@login_required
def delete_job(jid):
    c=db()
    j=c.execute("""SELECT j.*, c.name customer_name, c.phone, c.address, c.email
                  FROM jobs j LEFT JOIN customers c ON c.id=j.customer_id WHERE j.id=?""",(jid,)).fetchone()
    if not j: c.close(); return "Not found",404
    items=[dict(x) for x in c.execute("SELECT * FROM items WHERE job_id=? ORDER BY id",(jid,)).fetchall()]
    payments=[dict(x) for x in c.execute("SELECT * FROM payments WHERE job_id=? ORDER BY id",(jid,)).fetchall()]
    subtotal,total,paid,balance=job_totals(c,jid,j["discount"])
    c.execute("""INSERT INTO deleted_jobs(
      original_job_id,job_no,serial,customer_name,phone,address,email,appliance,brand,model,complaint,
      accessories,condition,technician,status,discount,received_at,delivered_at,deleted_at,deleted_by,
      subtotal,total,paid,balance,items_json,payments_json
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
      j["id"],j["job_no"],j["serial"],j["customer_name"],j["phone"],j["address"],j["email"],
      j["appliance"],j["brand"],j["model"],j["complaint"],j["accessories"],j["condition"],
      j["technician"],j["status"],j["discount"],j["received_at"],j["delivered_at"],now(),
      session.get("user","system"),subtotal,total,paid,balance,json.dumps(items),json.dumps(payments)))
    c.execute("UPDATE serial_registry SET job_id=NULL WHERE job_id=?",(jid,))
    c.execute("DELETE FROM jobs WHERE id=?",(jid,))
    c.commit(); c.close(); log("Deleted job","job",jid); flash("Job card archived and deleted","ok")
    return redirect(url_for("dashboard"))

@app.route("/jobs/<int:jid>/revoke", methods=["POST"])
@login_required
def revoke_job(jid):
    c=db(); j=c.execute("SELECT status FROM jobs WHERE id=?",(jid,)).fetchone()
    if not j: c.close(); return "Not found",404
    c.execute("UPDATE jobs SET status='Received', delivered_at=NULL WHERE id=?",(jid,))
    c.commit(); c.close(); log("Revoked job status","job",jid); flash("Job revoked and moved back to Received","ok")
    return redirect(url_for("job",jid=jid))

@app.route("/jobs/<int:jid>/item",methods=["POST"])
@login_required
def add_item(jid):
    c=db()
    inv_id=request.form.get("inventory_id") or None
    desc=request.form.get("description","")
    hsn=request.form.get("hsn","")
    qty=float(request.form.get("qty") or 1); rate=float(request.form.get("rate") or 0); kind=request.form.get("kind","Service")
    if inv_id:
        inv=c.execute("SELECT * FROM inventory WHERE id=?",(inv_id,)).fetchone()
        if not inv: c.close(); return "Inventory item not found",400
        if inv["stock"]<qty: c.close(); flash("Insufficient stock","error"); return redirect(url_for("job",jid=jid))
        desc,hsn,rate=inv["name"],inv["hsn"] or "",inv["sale"] or rate
        c.execute("UPDATE inventory SET stock=stock-? WHERE id=?",(qty,inv_id))
        kind="Part"
    c.execute("INSERT INTO items(job_id,description,hsn,qty,rate,kind,inventory_id) VALUES(?,?,?,?,?,?,?)",
              (jid,desc,hsn,qty,rate,kind,inv_id))
    c.commit(); c.close(); log("Added item","job",jid); return redirect(url_for("job",jid=jid))

@app.route("/items/<int:iid>/delete",methods=["POST"])
@login_required
def del_item(iid):
    c=db(); it=c.execute("SELECT * FROM items WHERE id=?",(iid,)).fetchone()
    if it:
        if it["inventory_id"]: c.execute("UPDATE inventory SET stock=stock+? WHERE id=?",(it["qty"],it["inventory_id"]))
        c.execute("DELETE FROM items WHERE id=?",(iid,)); c.commit(); log("Deleted item","item",iid)
    c.close(); return redirect(request.referrer or url_for("dashboard"))

@app.route("/jobs/<int:jid>/payment",methods=["POST"])
@login_required
def payment(jid):
    amt=float(request.form.get("amount") or 0)
    if amt>0:
        c=db(); c.execute("INSERT INTO payments(job_id,amount,method,reference,paid_at) VALUES(?,?,?,?,?)",
                          (jid,amt,request.form.get("method","Cash"),request.form.get("reference"),now())); c.commit(); c.close(); log("Added payment","job",jid)
    return redirect(url_for("job",jid=jid))

@app.route("/inventory",methods=["GET","POST"])
@login_required
def inventory():
    c=db()
    if request.method=="POST":
        c.execute("""INSERT INTO inventory(sku,name,hsn,stock,min_stock,purchase,sale,unit) VALUES(?,?,?,?,?,?,?,?)""",
                  (request.form["sku"],request.form["name"],request.form.get("hsn"),float(request.form.get("stock") or 0),
                   float(request.form.get("min_stock") or 0),float(request.form.get("purchase") or 0),float(request.form.get("sale") or 0),request.form.get("unit","pcs")))
        c.commit(); flash("Inventory item added","ok")
    rows=c.execute("SELECT * FROM inventory ORDER BY name").fetchall(); c.close()
    return render_template("inventory.html",inventory=rows)

@app.route("/expenses",methods=["GET","POST"])
@login_required
def expenses():
    c=db()
    if request.method=="POST":
        c.execute("INSERT INTO expenses(title,amount,category,spent_at,note) VALUES(?,?,?,?,?)",
                  (request.form["title"],float(request.form.get("amount") or 0),request.form.get("category"),request.form.get("spent_at") or now(),request.form.get("note")))
        c.commit(); flash("Expense added","ok")
    rows=c.execute("SELECT * FROM expenses ORDER BY id DESC").fetchall(); c.close()
    return render_template("expenses.html",expenses=rows)

@app.route("/reports")
@login_required
def reports():
    c=db()
    data={
      "jobs":c.execute("SELECT status,COUNT(*) n FROM jobs GROUP BY status").fetchall(),
      "revenue":c.execute("SELECT COALESCE(SUM(amount),0) n FROM payments").fetchone()["n"],
      "expenses":c.execute("SELECT COALESCE(SUM(amount),0) n FROM expenses").fetchone()["n"],
      "parts":c.execute("SELECT COALESCE(SUM(qty*rate),0) n FROM items WHERE kind='Part'").fetchone()["n"]
    }
    c.close(); return render_template("reports.html",data=data)

@app.route("/reports/delivered")
@login_required
def delivered_report():
    c=db(); rows=report_job_rows(c,status="Delivered"); c.close()
    return render_template("report_customers.html", title="Total Delivered", subtitle="Delivered jobs with customer details and current outstanding balance.", rows=rows, mode="delivered", export_url=url_for("export_delivered_csv"))

@app.route("/reports/ready")
@login_required
def ready_report():
    c=db(); rows=report_job_rows(c,status="Ready"); c.close()
    return render_template("report_customers.html", title="Ready for Delivery", subtitle="Jobs marked ready for customer collection.", rows=rows, mode="ready", export_url=url_for("export_report_csv",kind="ready"))

@app.route("/reports/repairing")
@login_required
def repairing_report():
    c=db(); rows=report_job_rows(c,status="Repairing"); c.close()
    return render_template("report_customers.html", title="Repair Jobs", subtitle="Jobs currently under repair.", rows=rows, mode="repairing", export_url=url_for("export_report_csv",kind="repairing"))

@app.route('/export/report/<kind>.csv')
@login_required
def export_report_csv(kind):
    status={'ready':'Ready','repairing':'Repairing'}.get(kind)
    if not status: return 'Invalid report',404
    c=db(); rows=report_job_rows(c,status=status); c.close()
    s=io.StringIO(); w=csv.writer(s); w.writerow(['Job No','Customer','Phone','Address','Appliance','Status','Total','Paid','Balance'])
    for r in rows: w.writerow([r['job_no'],r['customer'],r['phone'],r['address'],r['appliance'],r['status'],f"{r['total']:.2f}",f"{r['paid']:.2f}",f"{r['balance']:.2f}"])
    return send_file(io.BytesIO(s.getvalue().encode('utf-8-sig')),as_attachment=True,download_name=f'{kind}_jobs.csv',mimetype='text/csv')

@app.route("/reports/pending-payments")
@login_required
def pending_payments_report():
    c=db(); rows=report_job_rows(c,pending_only=True); c.close()
    return render_template("report_customers.html", title="Pending Payments", subtitle="Customers and jobs with an outstanding balance.", rows=rows, mode="pending", export_url=url_for("export_pending_payments_csv"))

@app.route("/export/pending-payments.csv")
@login_required
def export_pending_payments_csv():
    c=db(); rows=report_job_rows(c,pending_only=True); c.close()
    s=io.StringIO(); w=csv.writer(s)
    w.writerow(["Job No","Customer","Phone","Address","Appliance","Status","Total","Paid","Current Job Balance","Customer Current Balance"])
    for r in rows:
        w.writerow([r["job_no"],r["customer"],r["phone"],r["address"],r["appliance"],r["status"],f'{r["total"]:.2f}',f'{r["paid"]:.2f}',f'{r["balance"]:.2f}',f'{r["customer_balance"]:.2f}'])
    return send_file(io.BytesIO(s.getvalue().encode("utf-8-sig")),as_attachment=True,download_name="pending_payments.csv",mimetype="text/csv")

def make_report_pdf(title, rows, filename):
    buf=io.BytesIO()
    doc=SimpleDocTemplate(buf,pagesize=A4,rightMargin=12*mm,leftMargin=12*mm,topMargin=12*mm,bottomMargin=12*mm)
    styles=getSampleStyleSheet()
    story=[Paragraph("PATEL ELECTRICALS WORKSHOP", styles["Title"]), Paragraph("Mobile: 7762297283", styles["Normal"]), Paragraph(title, styles["Heading2"]), Spacer(1,8)]
    data=[["Job No","Customer / Phone","Appliance","Status","Total","Paid","Balance"]]
    for r in rows:
        customer=(r.get("customer") or "—") + " / " + (r.get("phone") or "—")
        data.append([r.get("job_no") or "—",customer,r.get("appliance") or "—",r.get("status") or "—",f"₹{r.get('total',0):.2f}",f"₹{r.get('paid',0):.2f}",f"₹{r.get('balance',0):.2f}"])
    table=Table(data,repeatRows=1,colWidths=[25*mm,42*mm,30*mm,25*mm,22*mm,22*mm,24*mm])
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#17324d')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('GRID',(0,0),(-1,-1),0.35,colors.grey),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),7),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f1f5f9')])]))
    story.append(table); story.append(Spacer(1,10)); story.append(Paragraph("PATEL ELECTRICALS WORKSHOP · Power • Precision • Trust · 7762297283", styles["Normal"]))
    doc.build(story); buf.seek(0)
    return send_file(buf,as_attachment=True,download_name=filename,mimetype='application/pdf')

@app.route('/export/report/<kind>.pdf')
@login_required
def export_report_pdf(kind):
    c=db()
    if kind=='pending': title='Pending Payments'; rows=report_job_rows(c,pending_only=True); filename='pending_payments.pdf'
    elif kind=='delivered': title='Delivered Jobs'; rows=report_job_rows(c,status='Delivered'); filename='delivered_jobs.pdf'
    elif kind=='ready': title='Ready for Delivery'; rows=report_job_rows(c,status='Ready'); filename='ready_for_delivery.pdf'
    elif kind=='repairing': title='Repair Jobs'; rows=report_job_rows(c,status='Repairing'); filename='repair_jobs.pdf'
    else: c.close(); return 'Invalid report',404
    c.close(); return make_report_pdf(title,rows,filename)

@app.route("/export/delivered.csv")
@login_required
def export_delivered_csv():
    c=db(); rows=report_job_rows(c,status="Delivered"); c.close()
    s=io.StringIO(); w=csv.writer(s)
    w.writerow(["Job No","Customer","Phone","Address","Appliance","Delivered At","Total","Paid","Current Job Balance","Customer Current Balance"])
    for r in rows:
        w.writerow([r["job_no"],r["customer"],r["phone"],r["address"],r["appliance"],r["delivered_at"],f'{r["total"]:.2f}',f'{r["paid"]:.2f}',f'{r["balance"]:.2f}',f'{r["customer_balance"]:.2f}'])
    return send_file(io.BytesIO(s.getvalue().encode("utf-8-sig")),as_attachment=True,download_name="delivered_jobs.csv",mimetype="text/csv")

@app.route("/job/<int:jid>/print")
@login_required
def print_job(jid):
    c=db(); j=c.execute("SELECT j.*,c.name customer,c.phone,c.address,c.email FROM jobs j LEFT JOIN customers c ON c.id=j.customer_id WHERE j.id=?",(jid,)).fetchone()
    items=c.execute("SELECT * FROM items WHERE job_id=? ORDER BY id",(jid,)).fetchall()
    pays=c.execute("SELECT * FROM payments WHERE job_id=? ORDER BY id",(jid,)).fetchall(); c.close()
    subtotal=sum((x["qty"] or 0)*(x["rate"] or 0) for x in items); total=max(0,subtotal-(j["discount"] or 0)); paid=sum(x["amount"] for x in pays)
    return render_template("print_job.html",j=j,items=items,subtotal=subtotal,total=total,paid=paid,balance=max(0,total-paid))

@app.route("/search")
@login_required
def search():
    q=request.args.get("q","").strip()
    c=db(); rows=c.execute("SELECT j.*, c.name customer FROM jobs j LEFT JOIN customers c ON c.id=j.customer_id WHERE j.serial LIKE ? OR j.job_no LIKE ? ORDER BY j.id DESC LIMIT 50",("%"+q+"%","%"+q+"%")).fetchall(); c.close()
    return render_template("search.html", rows=rows, q=q)

@app.route("/jobs/<int:jid>/stickers")
@login_required
def stickers(jid):
    c=db(); j=c.execute("SELECT j.*,c.name customer FROM jobs j LEFT JOIN customers c ON c.id=j.customer_id WHERE j.id=?",(jid,)).fetchone(); c.close()
    if not j: return "Not found",404
    return render_template("stickers.html", j=j)

@app.route("/print/job-cards")
@login_required
def blank_job_cards():
    return render_template("blank_job_cards.html", card_count=6)

@app.route("/barcode/<path:value>.svg")
@login_required
def barcode_svg(value):
    value=(value or "").strip()[:80]
    if not value: return "", 400
    drawing=createBarcodeDrawing("Code128", value=value, barHeight=27, barWidth=0.57, humanReadable=False)
    return renderSVG.drawToString(drawing), 200, {"Content-Type":"image/svg+xml; charset=utf-8","Cache-Control":"no-store"}

@app.route("/qr/<path:value>.png")
@login_required
def qr_png(value):
    value=(value or "").strip()[:80]
    if not value: return "", 400
    img=qrcode.make(value)
    out=io.BytesIO(); img.save(out, format="PNG"); out.seek(0)
    return send_file(out, mimetype="image/png", max_age=0)

@app.route("/print/sticker-sheet")
@login_required
def sticker_sheet():
    """Print the pre-generated serial pool, two physical stickers per serial.
    A 65-position sheet contains 32 serials (64 stickers) plus one blank slot.
    Serial rows are created independently of jobs and become allocated later by scanning.
    """
    c=db()
    serials=c.execute("""SELECT sr.serial, sr.job_id, sr.created_at,
                              j.job_no, j.appliance
                       FROM serial_registry sr
                       LEFT JOIN jobs j ON j.id=sr.job_id
                       ORDER BY sr.id""").fetchall()
    c.close()
    serials_per_sheet=32
    page_no=max(1, int(request.args.get('page', 1) or 1))
    total_pages=max(1, (len(serials)+serials_per_sheet-1)//serials_per_sheet)
    page_no=min(page_no, total_pages)
    selected=serials[(page_no-1)*serials_per_sheet:page_no*serials_per_sheet]
    stickers=[]
    for sr in selected:
        for copy_no in (1,2):
            stickers.append({"serial":sr["serial"],"job_no":sr["job_no"] or "UNALLOCATED",
                             "appliance":sr["appliance"] or "MOTOR", "copy":copy_no,
                             "allocated":bool(sr["job_id"])})
    return render_template("sticker_sheet.html", page=stickers, page_no=page_no,
                           total=len(serials)*2, total_serials=len(serials),
                           total_pages=total_pages, page_size=65,
                           serials_per_sheet=serials_per_sheet)

@app.route("/sticker-series/generate", methods=["POST"])
@login_required
def generate_sticker_series():
    """Create the next 32 unused serials, which print as 64 stickers + 1 blank slot."""
    try:
        count=max(1, min(500, int(request.form.get("count") or 32)))
    except ValueError:
        count=32
    c=db()
    created=[]
    for _ in range(count):
        serial=next_serial(c)
        c.execute("INSERT INTO serial_registry(serial,job_id,created_at) VALUES(?,?,?)",(serial,None,now()))
        created.append(serial)
    c.commit(); c.close()
    log(f"Generated sticker series ({len(created)} serials)", "serial_registry")
    flash(f"Generated {len(created)} serials / {len(created)*2} stickers.", "ok")
    return redirect(url_for("sticker_sheet", page=999999))

@app.route("/api/mobile/allocate", methods=["POST"])
@login_required
def mobile_allocate():
    """Allocate an unassigned preprinted serial to a new job from a scan."""
    data=request.get_json(silent=True) or request.form
    serial=(data.get("serial") or data.get("q") or "").strip()
    if not serial:
        return jsonify({"error":"Missing serial / barcode value"}),400
    c=db()
    registry=c.execute("SELECT * FROM serial_registry WHERE serial=?",(serial,)).fetchone()
    if not registry:
        c.close(); return jsonify({"error":"Serial not found. Generate a sticker series first."}),404
    if registry["job_id"]:
        c.close(); return jsonify({"error":"This serial is already allocated", "job_id":registry["job_id"]}),409
    customer_name=(data.get("customer_name") or "Walk-in / Pending").strip()
    phone=(data.get("phone") or "").strip()
    c.execute("INSERT INTO customers(name,phone,created_at) VALUES(?,?,?)",(customer_name,phone,now()))
    customer_id=c.execute("SELECT last_insert_rowid()").fetchone()[0]
    job_no=serial
    c.execute("""INSERT INTO jobs(job_no,customer_id,appliance,brand,serial,complaint,technician,status,received_at)
                 VALUES(?,?,?,?,?,?,?,?,?)""",
              (job_no,customer_id,data.get("appliance") or "Motor",data.get("brand") or "",
               serial,data.get("complaint") or "",data.get("technician") or "Harishankar Patel",
               "Received",now()))
    jid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.execute("UPDATE serial_registry SET job_id=? WHERE id=?",(jid,registry["id"]))
    ts=now()
    c.execute("INSERT INTO scan_events(job_id,serial,scan_type,scanned_by,scanned_at,scanned_date,device) VALUES(?,?,?,?,?,?,?)",
              (jid,serial,"allocation",session.get("user","unknown"),ts,ts[:10],request.headers.get("User-Agent","")[:250]))
    c.execute("UPDATE jobs SET last_scanned_at=?, scan_count=1 WHERE id=?",(ts,jid))
    c.commit(); c.close(); log("Allocated scanned serial", "job", jid)
    return jsonify({"ok":True,"job_id":jid,"job_no":job_no,"serial":serial,"message":"Serial allocated"}),201

@app.route("/backup")
@login_required
def backup():
    os.makedirs(os.path.join(BASE,"backups"),exist_ok=True)
    dest=os.path.join(BASE,"backups",f"patel_pos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(DB,dest); return send_file(dest,as_attachment=True,download_name=os.path.basename(dest))

@app.route("/restore", methods=["GET", "POST"])
@login_required
def restore():
    if session.get("role") != "admin":
        flash("Only an administrator can restore a backup.", "error")
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        upload=request.files.get("backup_file")
        if not upload or not upload.filename:
            flash("Please choose a SQLite backup file.", "error")
            return redirect(url_for("restore"))
        os.makedirs(os.path.join(BASE,"backups"),exist_ok=True)
        temp=os.path.join(os.path.join(BASE,"backups"), "_restore_upload.db")
        upload.save(temp)
        valid=False
        check=None
        try:
            check=sqlite3.connect(temp)
            result=check.execute("PRAGMA integrity_check").fetchone()[0]
            tables={r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            required={"users","customers","jobs","items","payments"}
            valid=(result=="ok" and required.issubset(tables))
        except sqlite3.DatabaseError:
            valid=False
        finally:
            if check: check.close()
        if not valid:
            try: os.remove(temp)
            except OSError: pass
            flash("Restore failed: invalid or incompatible SQLite backup.", "error")
            return redirect(url_for("restore"))
        current=os.path.join(BASE,"backups",f"before_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        try:
            shutil.copy2(DB,current)
            os.replace(temp,DB)
            init_db()
            log("Restored database backup", "database")
            flash("Backup restored successfully. Please verify your records.", "ok")
            return redirect(url_for("dashboard"))
        except Exception as exc:
            try:
                if os.path.exists(current): shutil.copy2(current,DB)
            except Exception:
                pass
            flash(f"Restore failed: {exc}", "error")
            return redirect(url_for("restore"))
    return render_template("restore.html")

@app.route("/export/customers.csv")
@login_required
def export_customers():
    c=db(); rows=c.execute("SELECT * FROM customers ORDER BY id").fetchall(); c.close()
    s=io.StringIO(); w=csv.writer(s); w.writerow(["ID","Name","Phone","Address","Email","Created"])
    for r in rows: w.writerow([r["id"],r["name"],r["phone"],r["address"],r["email"],r["created_at"]])
    return send_file(io.BytesIO(s.getvalue().encode()),as_attachment=True,download_name="customers.csv",mimetype="text/csv")

@app.route("/api/search")
@login_required
def api_search():
    q=request.args.get("q","")
    c=db(); rows=c.execute("SELECT * FROM inventory WHERE name LIKE ? OR sku LIKE ? LIMIT 20",("%"+q+"%","%"+q+"%")).fetchall(); c.close()
    return jsonify([dict(x) for x in rows])


@app.route('/mobile')
@login_required
def mobile():
    return render_template('mobile.html')

@app.route('/api/mobile/scan-barcode', methods=['POST'])
@login_required
def mobile_scan_barcode():
    """Decode a camera-captured barcode image on the POS computer.
    This works over plain local HTTP and is a fallback for browsers that block
    getUserMedia on a non-HTTPS LAN address.
    """
    if cv2 is None or np is None:
        return jsonify({'error':'Server barcode decoder is not installed. Install requirements.txt and restart POS.'}),503
    upload=request.files.get('image')
    if not upload or not upload.filename:
        return jsonify({'error':'No barcode image received'}),400
    raw=upload.read()
    if not raw or len(raw)>8*1024*1024:
        return jsonify({'error':'Invalid or oversized image'}),400
    arr=np.frombuffer(raw,dtype=np.uint8)
    image=cv2.imdecode(arr,cv2.IMREAD_COLOR)
    if image is None:
        return jsonify({'error':'Could not read the camera image'}),400

    # Primary decoder: pyzbar/zbar handles Code128 very well.
    if pyzbar_decode is not None:
        try:
            decoded=pyzbar_decode(image)
            for item in decoded:
                value=item.data.decode('utf-8','ignore').strip()
                if value:
                    return jsonify({'ok':True,'serial':value[:80],'type':str(item.type or 'CODE128'),'values':[value[:80]]})
        except Exception:
            pass

    # Secondary decoder: OpenCV BarcodeDetector.
    detector=cv2.barcode_BarcodeDetector()
    attempts=[image]
    if max(image.shape[:2])<1800:
        scale=1800/max(image.shape[:2])
        attempts.append(cv2.resize(image,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC))
    attempts.append(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY))
    for candidate in attempts:
        try:
            ok, infos, types, _=detector.detectAndDecodeWithType(candidate)
        except Exception:
            continue
        values=[(v or '').strip() for v in infos if isinstance(v,str) and v.strip()]
        if ok and values:
            serial=values[0][:80]
            return jsonify({'ok':True,'serial':serial,'type':(types[0] if types else 'CODE128'),'values':values})
    return jsonify({'error':'No readable barcode found. Take a closer, well-lit photo of the barcode.'}),422

@app.route('/api/mobile/job')
@login_required
def mobile_job_lookup():
    q=request.args.get('q','').strip()
    if not q: return jsonify({'error':'Missing barcode or serial'}),400
    c=db(); j=c.execute('''SELECT j.*, c.name customer, c.phone, c.address, c.email
        FROM jobs j LEFT JOIN customers c ON c.id=j.customer_id
        WHERE j.serial=? OR j.job_no=? LIMIT 1''',(q,q)).fetchone()
    if not j:
        sr=c.execute("SELECT * FROM serial_registry WHERE serial=?",(q,)).fetchone()
        if sr:
            ts=now(); c.execute("INSERT INTO scan_events(job_id,serial,scan_type,scanned_by,scanned_at,scanned_date,device) VALUES(?,?,?,?,?,?,?)",
                (None,q,'preallocation',session.get('user','unknown'),ts,ts[:10],request.headers.get('User-Agent','')[:250]))
            c.commit(); c.close()
            return jsonify({'unallocated':True,'serial':q,'message':'Preprinted serial found. Enter details to allocate it.'})
        c.close(); return jsonify({'error':'Serial not found'}),404
    ts=now()
    c.execute("INSERT INTO scan_events(job_id,serial,scan_type,scanned_by,scanned_at,scanned_date,device) VALUES(?,?,?,?,?,?,?)",
              (j['id'],j['serial'],'allocation',session.get('user','unknown'),ts,ts[:10],request.headers.get('User-Agent','')[:250]))
    c.execute("UPDATE jobs SET last_scanned_at=?, scan_count=COALESCE(scan_count,0)+1 WHERE id=?",(ts,j['id']))
    c.commit()
    updated=c.execute('''SELECT j.*, c.name customer, c.phone, c.address, c.email
        FROM jobs j LEFT JOIN customers c ON c.id=j.customer_id WHERE j.id=?''',(j['id'],)).fetchone()
    c.close(); log('Barcode allocation scan','job',j['id'])
    return jsonify(dict(updated))

@app.route('/api/mobile/job/<int:jid>/scan-history')
@login_required
def mobile_scan_history(jid):
    c=db(); rows=c.execute("SELECT * FROM scan_events WHERE job_id=? ORDER BY id DESC LIMIT 100",(jid,)).fetchall(); c.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/mobile/job/<int:jid>', methods=['POST'])
@login_required
def mobile_job_update(jid):
    data=request.get_json(silent=True) or request.form
    c=db(); j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
    if not j: c.close(); return jsonify({'error':'Job not found'}),404
    allowed=['appliance','brand','model','complaint','technician','status','discount']
    vals={k:data.get(k) for k in allowed if data.get(k) is not None}
    if 'status' in vals and vals['status']=='Delivered':
        vals['delivered_at']=now()
    if vals:
        sets=', '.join(f'{k}=?' for k in vals)
        c.execute(f'UPDATE jobs SET {sets} WHERE id=?',(*vals.values(),jid))
    customer_fields={k:data.get(k) for k in ['customer_name','phone','address','email'] if data.get(k) is not None}
    if customer_fields:
        customer_id=j['customer_id']
        if customer_id:
            sets=', '.join(f'{"name" if k=="customer_name" else k}=?' for k in customer_fields)
            c.execute(f'UPDATE customers SET {sets} WHERE id=?',(*customer_fields.values(),customer_id))
    c.commit(); c.close(); log('Mobile updated job','job',jid)
    return jsonify({'ok':True,'job_id':jid})

init_db()
if __name__=="__main__":
    app.run(host="0.0.0.0",port=5000,debug=False)
