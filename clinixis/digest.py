"""
Clinixis Weekly Expiry Digest
─────────────────────────────
Run this script weekly via a cron job or Windows Task Scheduler
to email expiry risk reports to all business owners.

Cron example (every Monday 7am):
  0 7 * * 1 cd /path/to/clinixis && python digest.py

Windows Task Scheduler:
  Action: python C:\path\to\clinixis\digest.py
  Trigger: Weekly, Monday, 07:00
"""

import smtplib
import os
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from database import get_db, get_expiry_digest

# ── Email config — set these as environment variables or edit here ──
SMTP_HOST   = os.environ.get('CLINIXIS_SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT   = int(os.environ.get('CLINIXIS_SMTP_PORT', '587'))
SMTP_USER   = os.environ.get('CLINIXIS_SMTP_USER', '')   # your Gmail address
SMTP_PASS   = os.environ.get('CLINIXIS_SMTP_PASS', '')   # Gmail app password

def build_html(digest):
    """Builds the HTML email body from digest data."""
    now = datetime.now().strftime('%d %b %Y, %I:%M %p')
    items_html = ''

    if digest['items']:
        rows = ''
        for item in digest['items']:
            days = item['days_left']
            day_class = ('color:#c0392b;font-weight:700' if days <= 7
                         else 'color:#e67e22;font-weight:600' if days <= 14
                         else 'color:#1a8a5a')
            rows += f"""
            <tr>
              <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">
                <strong>{item['product_name']}</strong><br>
                <span style="font-size:11px;color:#6b7280;">{item.get('category','')}</span>
              </td>
              <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;font-size:12px;color:#6b7280;">{item['batch_number']}</td>
              <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">{item['expiry_date']}</td>
              <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;{day_class}">{days}d</td>
              <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">{item['quantity_remaining']}</td>
              <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;font-weight:700;color:#0f4f8a;">&#8358;{item['risk_exposure']:,.0f}</td>
            </tr>"""

        items_html = f"""
        <div style="background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:14px 16px;margin-bottom:20px;">
          <div style="font-size:11px;font-weight:700;color:#856404;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Total Financial Risk Exposure</div>
          <div style="font-size:28px;font-weight:800;color:#856404;">&#8358;{digest['total_risk']:,.0f}</div>
          <div style="font-size:12px;color:#856404;margin-top:2px;">{len(digest['items'])} batch(es) at risk — stock value that may expire unsold</div>
        </div>
        <table style="width:100%;border-collapse:collapse;">
          <thead>
            <tr style="background:#f0f4f8;">
              <th style="padding:8px 12px;font-size:11px;font-weight:700;color:#6b7280;text-align:left;text-transform:uppercase;">Product</th>
              <th style="padding:8px 12px;font-size:11px;font-weight:700;color:#6b7280;text-align:left;text-transform:uppercase;">Batch</th>
              <th style="padding:8px 12px;font-size:11px;font-weight:700;color:#6b7280;text-align:left;text-transform:uppercase;">Expiry</th>
              <th style="padding:8px 12px;font-size:11px;font-weight:700;color:#6b7280;text-align:left;text-transform:uppercase;">Days</th>
              <th style="padding:8px 12px;font-size:11px;font-weight:700;color:#6b7280;text-align:left;text-transform:uppercase;">Qty</th>
              <th style="padding:8px 12px;font-size:11px;font-weight:700;color:#6b7280;text-align:left;text-transform:uppercase;">Risk</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        <div style="margin-top:16px;padding:12px;background:#e8f2fb;border-radius:8px;font-size:13px;color:#0f4f8a;">
          <strong>Recommended action:</strong> Review items with 7 or fewer days remaining immediately.
          Consider running promotions or returning stock to supplier where possible.
        </div>"""
    else:
        items_html = '<div style="text-align:center;padding:32px;color:#6b7280;font-size:14px;">No items expiring within the next 30 days. Stock is healthy.</div>'

    pharmacy_name = digest['pharmacy'].get('name', 'Your Pharmacy')
    return f"""
    <!DOCTYPE html><html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f4f8;margin:0;padding:20px;">
    <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
      <div style="background:#0f4f8a;padding:28px 28px 20px;">
        <div style="font-size:24px;font-weight:800;color:white;margin-bottom:4px;">Clini<span style="color:#64b5f6;">xis</span></div>
        <div style="color:rgba(255,255,255,0.85);font-size:14px;">Weekly Expiry Risk Digest &mdash; {pharmacy_name}<br>Generated {now} &mdash; Items expiring within 30 days</div>
      </div>
      <div style="padding:24px 28px;">{items_html}</div>
      <div style="background:#f0f4f8;padding:16px 28px;font-size:12px;color:#6b7280;border-top:1px solid #e2e8f0;">
        This digest is generated automatically by Clinixis every week. Log in to your dashboard to manage expiring stock.
      </div>
    </div>
    </body></html>"""


def send_digest(pharmacy_id, to_email, dry_run=False):
    """Send the expiry digest for one pharmacy."""
    digest = get_expiry_digest(pharmacy_id)
    pharmacy_name = digest['pharmacy'].get('name', 'Clinixis')
    html = build_html(digest)

    subject = (f"[Clinixis] Weekly Expiry Digest — {len(digest['items'])} item(s) at risk | "
               f"₦{digest['total_risk']:,.0f} exposure") if digest['items'] else \
              f"[Clinixis] Weekly Expiry Digest — {pharmacy_name} — All Clear"

    if dry_run:
        print(f"DRY RUN — Would send to: {to_email}")
        print(f"Subject: {subject}")
        print(f"Items at risk: {len(digest['items'])}")
        print(f"Total risk: ₦{digest['total_risk']:,.0f}")
        return

    if not SMTP_USER or not SMTP_PASS:
        print("ERROR: SMTP credentials not configured. Set CLINIXIS_SMTP_USER and CLINIXIS_SMTP_PASS.")
        print("Digest data preview:")
        print(f"  Items: {len(digest['items'])}, Risk: ₦{digest['total_risk']:,.0f}")
        return

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"Clinixis <{SMTP_USER}>"
    msg['To'] = to_email
    msg.attach(MIMEText(html, 'html'))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, to_email, msg.as_string())
    print(f"Digest sent to {to_email}")


def run_all():
    """Send digests to all pharmacy owners."""
    conn = get_db()
    pharmacies = conn.execute("SELECT id, email, name FROM pharmacies").fetchall()
    conn.close()

    dry_run = '--dry-run' in sys.argv
    if dry_run:
        print("=== DRY RUN MODE — no emails will be sent ===\n")

    sent = 0
    for pharmacy in pharmacies:
        print(f"Processing: {pharmacy['name']} ({pharmacy['email']})")
        try:
            send_digest(pharmacy['id'], pharmacy['email'], dry_run=dry_run)
            sent += 1
        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\nDone. {sent}/{len(pharmacies)} digests processed.")


if __name__ == '__main__':
    run_all()
