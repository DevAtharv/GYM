from flask import Flask, render_template, request, session, redirect, url_for
import os
from datetime import date, datetime
import qrcode
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ---------------- APP SETUP ----------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-me")

# ---------------- GOOGLE SHEETS ----------------
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

import json

creds_json = json.loads(os.environ["GOOGLE_CREDS"])

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    creds_json, scope
)

client = gspread.authorize(creds)

db = client.open("GymDB")
members_sheet = db.worksheet("members")
attendance_sheet = db.worksheet("attendance")

# ---------------- HELPERS ----------------
def generate_member_id():
    rows = members_sheet.get_all_records()
    return f"M{len(rows) + 1:03d}"

QR_FOLDER = os.path.join(app.root_path, "static", "qr")
os.makedirs(QR_FOLDER, exist_ok=True)

# ---------------- CONTEXT ----------------
@app.context_processor
def inject_now():
    return {'now': datetime.now}

# ---------------- ROUTES ----------------
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/dashboard")
def dashboard():
    members = members_sheet.get_all_records()
    attendance = attendance_sheet.get_all_records()

    today = date.today()
    today_str = today.isoformat()

    total_members = len(members)
    active = expired = 0
    revenue = 0

    for m in members:
        end = datetime.strptime(m["end_date"], "%Y-%m-%d").date()
        if end >= today:
            active += 1
        else:
            expired += 1

        revenue += int(m["fees"])

    # Attendance last 7 days
    attendance_map = {}
    for a in attendance:
        attendance_map[a["date"]] = attendance_map.get(a["date"], 0) + 1

    chart_dates = sorted(attendance_map.keys())[-7:]
    chart_counts = [attendance_map[d] for d in chart_dates]

    today_attendance = attendance_map.get(today_str, 0)

    return render_template(
        "dashboard.html",
        total_members=total_members,
        active=active,
        expired=expired,
        today_attendance=today_attendance,
        members=members,
        chart_dates=chart_dates,
        chart_counts=chart_counts,
        revenue=revenue
    )

@app.route("/add", methods=["GET", "POST"])
def add_member():
    if request.method == "POST":
        member_id = generate_member_id()

        name = request.form["name"]
        phone = request.form["phone"]
        fees = request.form["fees"]
        end_date = request.form["end_date"]

        start_date = date.today().isoformat()

        members_sheet.append_row([
            member_id,
            name,
            phone,
            "-",          # plan removed (custom date)
            fees,
            start_date,
            end_date
        ])

        return redirect(url_for("generate_qr", member_id=member_id))

    return render_template(
        "add_member.html",
        today=date.today().isoformat()
    )

@app.route("/generate_qr/<member_id>")
def generate_qr(member_id):
    qr_url = url_for("checkin", member_id=member_id, _external=True)

    img = qrcode.make(qr_url)
    qr_path = os.path.join(QR_FOLDER, f"{member_id}.png")
    img.save(qr_path)

    return render_template(
        "qr.html",
        member_id=member_id,
        qr_file=f"qr/{member_id}.png"
    )

# ---------------- ENTRY + EXIT LOGIC ----------------
@app.route("/checkin/<member_id>")
def checkin(member_id):
    today = date.today().isoformat()
    now_time = datetime.now().strftime("%H:%M:%S")

    records = attendance_sheet.get_all_records()

    # Loop with row index (important for updating exit time)
    for index, row in enumerate(records, start=2):
        if row["member_id"] == member_id and row["date"] == today:

            # ENTRY EXISTS BUT EXIT NOT DONE → MARK EXIT
            if row["exit_time"] in ("", None):
                attendance_sheet.update_cell(index, 4, now_time)
                return "<h2 style='color:blue'>🚪 Exit time marked successfully</h2>"

            # ENTRY + EXIT BOTH DONE
            return "<h2>✅ Attendance already completed for today</h2>"

    # NO RECORD FOUND → MARK ENTRY
    attendance_sheet.append_row([
        member_id,
        today,
        now_time,
        ""
    ])

    return "<h2 style='color:green'>🏋️ Entry time marked successfully</h2>"

@app.route("/members")
def members_list():
    records = members_sheet.get_all_records()
    return render_template("members.html", members=records)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

# ---------------- RUN ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
