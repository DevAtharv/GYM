from flask import Flask, render_template, request, redirect, url_for, session, flash
import os
import json
from datetime import date, datetime, timedelta
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
attendance_sheet = db.worksheet("attendance")

# --- NEW: PAYMENTS SHEET SETUP & MIGRATION ---
# This block automatically creates a 'payments' sheet to track history
try:
    payments_sheet = db.worksheet("payments")
except gspread.WorksheetNotFound:
    # Create payments sheet if it doesn't exist
    payments_sheet = db.add_worksheet(title="payments", rows=1000, cols=6)
    payments_sheet.append_row(["transaction_id", "member_id", "amount", "date", "type", "notes"])

# Automatic Migration: Backfill payments if empty but members exist
# This ensures you don't lose your current revenue numbers
if len(payments_sheet.get_all_values()) <= 1:
    print("Migrating existing member fees to payments ledger...")
    existing_members = members_sheet.get_all_records()
    
    for m in existing_members:
        try:
            fee = m.get("fees", 0)
            start = m.get("start_date", date.today().isoformat())
            m_id = m.get("member_id")
            # Create a historical transaction for their joining
            if fee and str(fee).isdigit() and int(fee) > 0:
                payments_sheet.append_row([
                    f"INIT_{m_id}", # Transaction ID
                    m_id,
                    fee,
                    start,
                    "Join (Migrated)",
                    "Auto-migrated from current status"
                ])
        except Exception as e:
            print(f"Skipped migrating member {m.get('member_id')}: {e}")

# ---------------- HELPERS ----------------
def generate_member_id():
    rows = members_sheet.get_all_values()
    # Subtract 1 for header, but handle case where sheet is empty/new
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
    return {
        "now": datetime.now,
        "today": date.today().isoformat()
    }

# ---------------- ROUTES ----------------
@app.route("/")
def home():
    if 'logged_in' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user_id = request.form.get("user_id")
        password = request.form.get("password")
        
        if user_id == ADMIN_ID and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['user_id'] = user_id
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials. Please try again.', 'error')
            return redirect(url_for('login'))
    
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

@app.route("/dashboard")
@login_required
def dashboard():
    members = members_sheet.get_all_records()
    attendance = attendance_sheet.get_all_records()
    
    # --- REVENUE CALCULATION FIXED (Uses Payments Sheet) ---
    # We now calculate revenue by summing up the 'payments' sheet
    payments = payments_sheet.get_all_records()
    
    monthly_revenue = defaultdict(float)
    total_revenue = 0
    
    for p in payments:
        try:
            amount = float(p.get("amount", 0))
            txn_date = p.get("date", "")
            
            if txn_date and amount > 0:
                # Extract YYYY-MM
                year_month = txn_date[:7]
                monthly_revenue[year_month] += amount
                total_revenue += amount
        except (ValueError, TypeError):
            continue

    # Sort months for chart
    sorted_months = sorted(monthly_revenue.keys())[-6:] if monthly_revenue else []
    revenue_months = sorted_months
    revenue_amounts = [monthly_revenue[month] for month in sorted_months]
    
    # Calculate specific period revenues
    current_month = date.today().strftime("%Y-%m")
    current_month_revenue = monthly_revenue.get(current_month, 0)
    
    try:
        last_month_date = date.today().replace(day=1) - timedelta(days=1)
        last_month = last_month_date.strftime("%Y-%m")
        last_month_revenue = monthly_revenue.get(last_month, 0)
    except:
        last_month_revenue = 0

    # --- ATTENDANCE CHART ---
    attendance_map = {}
    for a in attendance:
        date_key = a.get("date", "")
        if date_key:
            attendance_map[date_key] = attendance_map.get(date_key, 0) + 1

    chart_dates = sorted(attendance_map.keys())[-7:]
    chart_counts = [attendance_map[d] for d in chart_dates]

    return render_template(
        "dashboard.html",
        members=members,
        chart_dates=chart_dates,
        chart_counts=chart_counts,
        total_revenue=total_revenue,
        current_month_revenue=current_month_revenue,
        last_month_revenue=last_month_revenue,
        revenue_months=revenue_months,
        revenue_amounts=revenue_amounts
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

        # 1. Add to Members Sheet (Current Status)
        members_sheet.append_row([
            member_id,
            request.form["name"],
            request.form["phone"],
            "-",
            fees,
            join_date,
            request.form["end_date"]
        ])

        # 2. Add to Payments Sheet (Financial History)
        payments_sheet.append_row([
            generate_txn_id(),
            member_id,
            fees,
            join_date,
            "Join",
            "Initial Membership"
        ])

        flash(f'Member {member_id} added successfully!', 'success')
        return redirect(url_for("qr_code", member_id=member_id))

    return render_template("add_member.html")

@app.route("/renew/<member_id>", methods=["GET", "POST"])
@login_required
def renew_membership(member_id):
    if request.method == "POST":
        all_members = members_sheet.get_all_records()
        
        for i, member in enumerate(all_members, start=2):
            if member.get("member_id") == member_id:
                new_end_date = request.form.get("new_end_date")
                new_fees = request.form.get("fees")
                
                # 1. Update Member Expiry in 'members' sheet
                members_sheet.update_cell(i, 7, new_end_date)
                # We update the 'fees' column too just to show their CURRENT plan cost,
                # but we rely on the payments sheet for totals.
                members_sheet.update_cell(i, 5, new_fees)
                
                # 2. Add NEW Transaction to Payments Sheet
                # This is the key fix: We record a NEW payment instead of overwriting.
                payments_sheet.append_row([
                    generate_txn_id(),
                    member_id,
                    new_fees,
                    date.today().isoformat(),
                    "Renewal",
                    f"Renewed until {new_end_date}"
                ])
                
                flash(f'Membership renewed for {member.get("name")}! Payment recorded.', 'success')
                return redirect(url_for('members'))
        
        flash('Member not found!', 'error')
        return redirect(url_for('members'))
    
    # GET request
    all_members = members_sheet.get_all_records()
    member = None
    for m in all_members:
        if m.get("member_id") == member_id:
            member = m
            break
    
    if not member:
        flash('Member not found!', 'error')
        return redirect(url_for('members'))
    
    return render_template("renew.html", member=member)

@app.route("/qr/<member_id>")
@login_required
def qr_code(member_id):
    checkin_url = url_for("checkin", member_id=member_id, _external=True)
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(checkin_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    qr_filename = f"{member_id}.png"
    qr_path = os.path.join(QR_FOLDER, qr_filename)
    img.save(qr_path)
    
    return render_template(
        "qr.html",
        member_id=member_id,
        qr_file=f"qr/{qr_filename}"
    )

@app.route("/checkin/<member_id>")
def checkin(member_id):
    today = date.today().isoformat()
    now_time = datetime.now().strftime("%H:%M:%S")

    records = attendance_sheet.get_all_records()

    for i, row in enumerate(records, start=2):
        if row.get("member_id") == member_id and row.get("date") == today:
            if not row.get("exit_time") or row.get("exit_time") == "":
                attendance_sheet.update_cell(i, 4, now_time)
                return render_template("checkin_success.html", 
                                      message="Exit Marked", 
                                      emoji="🚪",
                                      member_id=member_id)
            return render_template("checkin_success.html", 
                                  message="Already Checked In Today", 
                                  emoji="✅",
                                  member_id=member_id)

    attendance_sheet.append_row([member_id, today, now_time, ""])
    return render_template("checkin_success.html", 
                          message="Entry Marked", 
                          emoji="🏋️",
                          member_id=member_id)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🏋️ Gym Management System starting on http://localhost:{port}")
    print(f"🔐 Admin Login: ID = '{ADMIN_ID}' | Password = '{ADMIN_PASSWORD}'")
    app.run(host="0.0.0.0", port=port, debug=True)
