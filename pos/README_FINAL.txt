PATEL ELECTRICALS WORKSHOP POS — A5 EDITION

Local workshop POS built for job cards, repair tracking, billing, payments, customers,
inventory, expenses, reports and database backup.

START WINDOWS:
1. Install Python 3.11+.
2. Double-click START_PATEL_POS.bat.
3. Open http://127.0.0.1:5000
4. First login: admin / admin
5. Change the password before real-world use (add a user-management layer if deploying to staff).

A5 PRINTING:
Open a job card and click "Print A5 Job Card". Browser print dialog -> Paper A5,
Scale 100%, margins Default/None as appropriate, Headers & Footers OFF.
This build intentionally has NO RP203/ESC-POS integration.

DATABASE:
patel_pos.db is created automatically. Use Backup DB from the sidebar for a downloadable SQLite backup.

LAN:
The server binds to 0.0.0.0:5000. From another PC on the same LAN use the host computer's
LAN IP, e.g. http://192.168.1.20:5000 (Windows Firewall may need an inbound rule).

NOTE:
This is a commercial-style functional local workshop POS starter, not certified accounting/GST software.
Invoice totals use item and labour charges less any discount.

SERIAL POOL WORKFLOW (V2.4 correction)
- Generate Next Series creates 32 unallocated serials = 64 stickers + 1 blank slot on a 65-position sheet.
- Each serial prints exactly two matching stickers.
- Scan any printed serial/barcode to allocate customer, appliance, brand, complaint and technician details.
- Allocation creates the job and links the serial_registry row; later scans update the linked job.

MOBILE / SAME-WIFI TROUBLESHOOTING (UPDATED):
1. Start with START_PATEL_POS_WIFI.bat and note the Mobile URL shown. The launcher now selects the active network adapter with a default gateway, avoiding incorrect virtual-adapter IPs.
2. On the phone, open that exact URL, for example http://192.168.1.20:5000.
3. Do NOT use 127.0.0.1 or localhost on the phone; those point to the phone itself.
4. Set the Windows Wi-Fi network profile to Private.
5. Run ALLOW_FIREWALL_5000.bat as Administrator once.
6. Ensure the phone and computer are on the same non-guest Wi-Fi network; disable AP/client isolation on the router if enabled.
7. If the PC has multiple network adapters, use the IPv4 address of the same Wi-Fi adapter as the phone.


V2.4 Update — Mobile Scanner & Financial Reports
- Barcode stickers encode the serial only. Motor/UNALLOCATED/copy text is removed from sticker output.
- Dashboard includes clickable Total Delivered and Pending Amount cards.
- Delivered and Pending Payments pages show customer details, job totals, paid amount, current job balance, and customer current balance.
- Pending Payments has CSV export. Delivered Jobs also has CSV export.
- Mobile Scanner supports a camera-capture workflow over local HTTP and a live camera workflow when the browser allows camera access.
- Server-side barcode image decoding uses OpenCV as a fallback, so the phone does not need native BarcodeDetector support.

HTTP LAN scanner note:
- On a plain http://LAN-IP:5000 address, mobile browsers block live getUserMedia camera preview for security reasons.
- The scanner therefore uses the phone's native camera capture workflow on HTTP, with a captured-image preview and retake option.
- Live in-page camera preview remains available when the POS is served over HTTPS.
