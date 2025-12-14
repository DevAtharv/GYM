from flask import Flask, render_template, request, redirect, url_for
import os, json
from datetime import date, datetime
import qrcode
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret")

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

    today = date.today()
    revenue = 0
    active = expired = 0

    for m in members:
        end = datetime.strptime(m["end_date"], "%Y-%m-%d").date()
        revenue += int(m["fees"])
        if end >= today:
            active += 1
        else:
            expired += 1

    # Attendance last 7 days
    attendance_map = {}
    for a in attendance:
        attendance_map[a["date"]] = attendance_map.get(a["date"], 0) + 1

    chart_dates = sorted(attendance_map.keys())[-7:]
    chart_counts = [attendance_map[d] for d in chart_dates]

    return render_template(
        "dashboard.html",
        members=members,
        revenue=revenue,
        active=active,
        expired=expired,
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

        return redirect(url_for("generate_qr", member_id=member_id))

    return render_template("add_member.html")

@app.route("/generate_qr/<member_id>")
def generate_qr(member_id):
    qr_url = url_for("checkin", member_id=member_id, _external=True)
    img = qrcode.make(qr_url)
    img.save(os.path.join(QR_FOLDER, f"{member_id}.png"))

    return render_template(
        "qr.html",
        member_id=member_id,
        qr_file=f"qr/{member_id}.png"
    )

@app.route("/checkin/<member_id>")
def checkin(member_id):
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M:%S")

    records = attendance_sheet.get_all_records()

    for i, r in enumerate(records, start=2):
        if r["member_id"] == member_id and r["date"] == today:
            if not r["exit_time"]:
                attendance_sheet.update_cell(i, 4, now)
                return "<h2>🚪 Exit marked</h2>"
            return "<h2>✅ Already completed today</h2>"

    attendance_sheet.append_row([member_id, today, now, ""])
    return "<h2>🏋️ Entry marked</h2>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
