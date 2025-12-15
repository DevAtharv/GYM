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
# You can change these or store in environment variables
ADMIN_ID = os.environ.get("ADMIN_ID", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "gym123")

# ---------------- GOOGLE SHEETS ----------------
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

# ---------------- HELPERS ----------------
def generate_member_id():
    rows = members_sheet.get_all_records()
    return f"M{len(rows)+1:03d}"

QR_FOLDER = os.path.join(app.root_path, "static", "qr")
os.makedirs(QR_FOLDER, exist_ok=True)

# Login required decorator
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

    # Attendance map
    attendance_map = {}
    for a in attendance:
        date_key = a.get("date", "")
        if date_key:
            attendance_map[date_key] = attendance_map.get(date_key, 0) + 1

    chart_dates = sorted(attendance_map.keys())[-7:]
    chart_counts = [attendance_map[d] for d in chart_dates]

    # Revenue Analysis
    monthly_revenue = defaultdict(float)
    total_revenue = 0
    
    for member in members:
        try:
            fees = float(member.get("fees", 0))
            start_date = member.get("start_date", "")
            
            if start_date and fees > 0:
                # Extract year-month from start_date (format: YYYY-MM-DD)
                year_month = start_date[:7]  # Gets YYYY-MM
                monthly_revenue[year_month] += fees
                total_revenue += fees
        except (ValueError, TypeError):
            continue
    
    # Sort by month and get last 6 months
    sorted_months = sorted(monthly_revenue.keys())[-6:] if monthly_revenue else []
    revenue_months = sorted_months
    revenue_amounts = [monthly_revenue[month] for month in sorted_months]
    
    # Calculate this month's revenue
    current_month = date.today().strftime("%Y-%m")
    current_month_revenue = monthly_revenue.get(current_month, 0)
    
    # Calculate last month's revenue
    try:
        last_month_date = date.today().replace(day=1) - timedelta(days=1)
        last_month = last_month_date.strftime("%Y-%m")
        last_month_revenue = monthly_revenue.get(last_month, 0)
    except:
        last_month_revenue = 0

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

        members_sheet.append_row([
            member_id,
            request.form["name"],
            request.form["phone"],
            "-",
            request.form["fees"],
            date.today().isoformat(),
            request.form["end_date"]
        ])

        flash(f'Member {member_id} added successfully!', 'success')
        return redirect(url_for("qr_code", member_id=member_id))

    return render_template("add_member.html")

@app.route("/renew/<member_id>", methods=["GET", "POST"])
@login_required
def renew_membership(member_id):
    if request.method == "POST":
        # Get all members
        all_members = members_sheet.get_all_records()
        
        # Find the member
        for i, member in enumerate(all_members, start=2):
            if member.get("member_id") == member_id:
                # Get new end date and fees from form
                new_end_date = request.form.get("new_end_date")
                new_fees = request.form.get("fees")
                
                # Update the end_date (column 7) and fees (column 5)
                members_sheet.update_cell(i, 7, new_end_date)
                members_sheet.update_cell(i, 5, new_fees)
                
                flash(f'Membership renewed for {member.get("name")} until {new_end_date}!', 'success')
                return redirect(url_for('members'))
        
        flash('Member not found!', 'error')
        return redirect(url_for('members'))
    
    # GET request - show renewal form
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
    """Generate and display QR code for a member"""
    # Generate QR code URL for check-in
    checkin_url = url_for("checkin", member_id=member_id, _external=True)
    
    # Create QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(checkin_url)
    qr.make(fit=True)
    
    # Save QR code image
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
    """Handle member check-in/check-out via QR code scan - No login required"""
    today = date.today().isoformat()
    now_time = datetime.now().strftime("%H:%M:%S")

    records = attendance_sheet.get_all_records()

    # Check if member already checked in today
    for i, row in enumerate(records, start=2):
        if row.get("member_id") == member_id and row.get("date") == today:
            # If exit_time is empty, mark exit
            if not row.get("exit_time") or row.get("exit_time") == "":
                attendance_sheet.update_cell(i, 4, now_time)
                return render_template("checkin_success.html", 
                                      message="Exit Marked", 
                                      emoji="🚪",
                                      member_id=member_id)
            # Already checked in and out
            return render_template("checkin_success.html", 
                                  message="Already Checked In Today", 
                                  emoji="✅",
                                  member_id=member_id)

    # New check-in for today
    attendance_sheet.append_row([member_id, today, now_time, ""])
    return render_template("checkin_success.html", 
                          message="Entry Marked", 
                          emoji="🏋️",
                          member_id=member_id)

# ---------------- RUN ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🏋️ Gym Management System starting on http://localhost:{port}")
    print(f"🔐 Admin Login: ID = '{ADMIN_ID}' | Password = '{ADMIN_PASSWORD}'")
    print(f"📱 QR codes will be saved to: {QR_FOLDER}\n")
    app.run(host="0.0.0.0", port=port, debug=True)
