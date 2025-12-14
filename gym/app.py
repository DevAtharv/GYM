from flask import Flask, render_template, request, redirect, url_for
import os
import json
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

@app.context_processor
def inject_now():
    return {"now": datetime.now}

# ---------------- ROUTES ----------------
@app.route("/")
def home():
    return redirect("/dashboard")

@app.route("/dashboard")
def dashboard():
    members = members_sheet.get_all_records()
    attendance = attendance_sheet.get_all_records()

    today = date.today().isoformat()

    attendance_map = {}
    for a in attendance:
        attendance_map[a["date"]] = attendance_map.get(a["date"], 0) + 1

    chart_dates = sorted(attendance_map.keys())[-7:]
    chart_counts = [attendance_map[d] for d in chart_dates]

    return render_template(
        "dashboard.html",
        members=members,
        chart_dates=chart_dates,
        chart_counts=chart_counts
    )

@app.route("/add", methods=["GET", "POST"])
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

        return redirect(url_for("dashboard"))

    return render_template("add_member.html")

@app.route("/generate_qr/<member_id>")
def generate_qr(member_id):
    qr_url = url_for("checkin", member_id=member_id, _external=True)

    img = qrcode.make(qr_url)
    path = os.path.join(QR_FOLDER, f"{member_id}.png")
    img.save(path)

    return render_template(
        "qr.html",
        member_id=member_id,
        qr_file=f"qr/{member_id}.png"
    )
@app.route("/checkin/<member_id>")
def checkin(member_id):
    today = date.today().isoformat()
    now_time = datetime.now().strftime("%H:%M:%S")

    records = attendance_sheet.get_all_records()

    for i, row in enumerate(records, start=2):
        if row["member_id"] == member_id and row["date"] == today:
            if row["exit_time"] == "":
                attendance_sheet.update_cell(i, 4, now_time)
                return "<h2>🚪 Exit marked</h2>"
            return "<h2>✅ Attendance already done</h2>"

    attendance_sheet.append_row([member_id, today, now_time, ""])
    return "<h2>🏋️ Entry marked</h2>"

# ---------------- RUN ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

