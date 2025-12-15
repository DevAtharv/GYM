from flask import Flask, render_template, request, redirect, url_for, session, flash
import os
import json
from datetime import date, datetime
from functools import wraps
from collections import defaultdict
import qrcode
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ---------------- APP SETUP ----------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "gym-secret-key-change-this-in-production")

# ---------------- ADMIN CREDENTIALS ----------------
ADMIN_ID = os.environ.get("ADMIN_ID", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "gym123")

# ---------------- GOOGLE SHEETS SETUP ----------------
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds_json = json.loads(os.environ["GOOGLE_CREDS"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
client = gspread.authorize(creds)

db = client.open("GymDB")
members_sheet = db.worksheet("members")

# --- PAYMENTS SHEET SETUP ---
try:
    payments_sheet = db.worksheet("payments")
except gspread.WorksheetNotFound:
    payments_sheet = db.add_worksheet(title="payments", rows=1000, cols=6)
    payments_sheet.append_row(["transaction_id", "member_id", "amount", "date", "type", "notes"])

# Automatic Migration
if len(payments_sheet.get_all_values()) <= 1:
    print("Migrating existing member fees to payments ledger...")
    existing_members = members_sheet.get_all_records()
    for m in existing_members:
        try:
            fee = m.get("fees", 0)
            start = m.get("start_date", date.today().isoformat())
            m_id = m.get("member_id")
            if fee and str(fee).isdigit() and int(fee) > 0:
                payments_sheet.append_row([f"INIT_{m_id}", m_id, fee, start, "Join (Migrated)", "Auto-migrated"])
        except:
            continue

# ---------------- HELPERS ----------------

def get_today_sheet():
    """
    Finds or creates a sheet specifically for TODAY'S date.
    Format: 'Attd_YYYY-MM-DD'
    """
    today_str = date.today().isoformat()
    sheet_name = f"Attd_{today_str}"
    
    try:
        # Try to open today's sheet
        return db.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        # If not found, create it with headers
        # We put 'name' in the sheet too, so you can just print this sheet easily
        new_sheet = db.add_worksheet(title=sheet_name, rows=500, cols=5)
        new_sheet.append_row(["member_id", "name", "in_time", "out_time", "date"])
        return new_sheet

def generate_member_id():
    rows = members_sheet.get_all_values()
    count = len(rows) - 1 if len(rows) > 0 else 0
    return f"M{count+1:03d}"

def generate_txn_id():
    return f"TXN{int(datetime.now().timestamp())}"

QR_FOLDER = os.path.join(app.root_path, "static", "qr")
os.makedirs(QR_FOLDER, exist_ok=True)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Please login to access this page', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.context_processor
def inject_now():
    return {"now": datetime.now, "today": date.today().isoformat()}

# ---------------- ROUTES ----------------
@app.route("/")
def home():
    return redirect(url_for('dashboard')) if 'logged_in' in session else redirect(url_for('login'))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("user_id") == ADMIN_ID and request.form.get("password") == ADMIN_PASSWORD:
            session['logged_in'] = True
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/dashboard")
@login_required
def dashboard():
    members = members_sheet.get_all_records()
    payments = payments_sheet.get_all_records()
    
    # 1. Get Today's Live Attendance
    # This automatically resets every day because it looks for a new sheet
    today_sheet = get_today_sheet()
    todays_records = today_sheet.get_all_records()
    
    # Reverse so the latest person is at the top
    todays_records.reverse()

    # 2. Simple Revenue Stats
    monthly_revenue = defaultdict(float)
    total_revenue = 0
    for p in payments:
        try:
            amount = float(p.get("amount", 0))
            txn_date = p.get("date", "")
            if txn_date and amount > 0:
                year_month = txn_date[:7]
                monthly_revenue[year_month] += amount
                total_revenue += amount
        except:
            continue

    current_month = date.today().strftime("%Y-%m")
    current_month_revenue = monthly_revenue.get(current_month, 0)
    
    # We pass 'todays_records' as 'recent_checkins' to reuse your template logic
    # But now it ONLY contains today's people.
    return render_template(
        "dashboard.html",
        members=members,
        recent_checkins=todays_records,
        total_revenue=total_revenue,
        current_month_revenue=current_month_revenue
    )

@app.route("/members")
@login_required
def members():
    members_list = members_sheet.get_all_records()
    return render_template("members.html", members=members_list)

@app.route("/add", methods=["GET", "POST"])
@login_required
def add_member():
    if request.method == "POST":
        member_id = generate_member_id()
        fees = request.form["fees"]
        join_date = date.today().isoformat()
        
        members_sheet.append_row([
            member_id, request.form["name"], request.form["phone"], 
            "-", fees, join_date, request.form["end_date"]
        ])
        
        payments_sheet.append_row([
            generate_txn_id(), member_id, fees, join_date, "Join", "Initial Membership"
        ])
        
        flash(f'Member {member_id} added!', 'success')
        return redirect(url_for("qr_code", member_id=member_id))
    return render_template("add_member.html")

@app.route("/renew/<member_id>", methods=["GET", "POST"])
@login_required
def renew_membership(member_id):
    if request.method == "POST":
        all_members = members_sheet.get_all_records()
        for i, m in enumerate(all_members, start=2):
            if m.get("member_id") == member_id:
                new_end = request.form.get("new_end_date")
                fees = request.form.get("fees")
                
                members_sheet.update_cell(i, 7, new_end)
                members_sheet.update_cell(i, 5, fees)
                
                payments_sheet.append_row([
                    generate_txn_id(), member_id, fees, date.today().isoformat(),
                    "Renewal", f"Renewed until {new_end}"
                ])
                flash('Renewed successfully!', 'success')
                return redirect(url_for('members'))
        flash('Member not found!', 'error')
    
    all_members = members_sheet.get_all_records()
    member = next((m for m in all_members if m.get("member_id") == member_id), None)
    return render_template("renew.html", member=member) if member else redirect(url_for('members'))

@app.route("/qr/<member_id>")
@login_required
def qr_code(member_id):
    checkin_url = url_for("checkin", member_id=member_id, _external=True)
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(checkin_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    filename = f"{member_id}.png"
    img.save(os.path.join(QR_FOLDER, filename))
    return render_template("qr.html", member_id=member_id, qr_file=f"qr/{filename}")

@app.route("/checkin/<member_id>")
def checkin(member_id):
    # 1. Get or Create TODAY'S sheet
    today_sheet = get_today_sheet()
    
    today_str = date.today().isoformat()
    now_time = datetime.now().strftime("%I:%M %p")
    
    # 2. Find Member Name (So we can save it in the daily sheet for easy printing)
    members = members_sheet.get_all_records()
    member_name = "Unknown"
    for m in members:
        if m.get("member_id") == member_id:
            member_name = m.get("name")
            break

    # 3. Check if they already checked in TODAY
    records = today_sheet.get_all_records()
    
    for i, row in enumerate(records, start=2):
        # We only check member_id in this specific daily sheet
        if row.get("member_id") == member_id:
            if not row.get("out_time"):
                today_sheet.update_cell(i, 4, now_time) # Update Exit Time
                return render_template("checkin_success.html", message="Exit Marked", emoji="🚪", member_id=member_id)
            return render_template("checkin_success.html", message="Already Checked In", emoji="✅", member_id=member_id)
            
    # 4. New Entry for Today
    today_sheet.append_row([member_id, member_name, now_time, "", today_str])
    return render_template("checkin_success.html", message="Entry Marked", emoji="🏋️", member_id=member_id)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
