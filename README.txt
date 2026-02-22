Staff Application Bot — README
Maintainer: CodeBullet (Yoshi12345700)
Version: 1.0
Last updated: 2026-02-22

Overview
This repository contains a single-file Discord bot (bot.py) that runs a DM-based
staff application flow. Applicants answer configurable questions in DMs. The bot
stores answers in SQLite, posts a staff review embed in a configured staff channel,
supports a pick/score/approve/deny workflow, and cleans up its own DM messages so
applicant DMs remain tidy. The bot preserves a single summary/result message in
the applicant DM so the applicant can see their Application ID and final result.

Files
- bot.py            : Main bot source (single-file Python)
- config.ini        : Configuration file (example below)
- requirements.txt  : Python dependencies
- LICENSE.txt       : License (Code Bullet Yoshi License CBY-1.0)
- README.txt        : This file

Configuration (config.ini)
Create a file named `config.ini` in the same folder as bot.py. Example:

[bot]
token = YOUR_BOT_TOKEN_HERE
guild_id = 0                      ; optional: restrict slash commands to a guild
staff_channel_id = 123456789012345678
reviewer_role_id = 234567890123456789
application_cooldown_seconds = 300

[questions]
count = 5
q1 = Why do you want to join staff?
q2 = How much moderation experience do you have?
q3 = What timezone are you in and what hours can you moderate?
q4 = Have you been staff on other servers? If so, list examples.
q5 = Anything else we should know?

[templates]
approved = Congrats — your application (ID {id}) has been approved. Reviewer: {reviewer}. Score: {score}/{scale}. Reason: {reason}
denied = We're sorry — your application (ID {id}) has been denied. Reviewer: {reviewer}. Score: {score}/{scale}. Reason: {reason}

Notes:
- Remove inline comments on numeric lines or keep them after a semicolon; the loader strips comments.
- Set `staff_channel_id` to the channel where staff review posts should appear.
- Set `reviewer_role_id` to the role ID allowed to pick/score/decide. Set to 0 to disable role checks.

Installation
Two hosting scenarios are covered: Home host (local machine) and Server host (VPS/Cloud).

Common prerequisites
- Python 3.10 or newer
- A Discord bot token (create an application at https://discord.com/developers)
- Bot invited to your server with scopes: bot, applications.commands
- Bot permissions: Send Messages, Embed Links, Use Slash Commands, View Channels, Manage Messages (optional but recommended for staff channel)

Home host (local machine)
1. Clone or copy files to a folder.
2. Create and edit `config.ini` with your values.
3. Create a virtual environment (recommended):
   - Windows:
     python -m venv venv
     venv\Scripts\activate
   - macOS / Linux:
     python3 -m venv venv
     source venv/bin/activate
4. Install dependencies:
   pip install -r requirements.txt
5. Run the bot:
   python bot.py

Server host (VPS / Cloud)
1. Upload files to your server.
2. Install Python 3.10+ and create a virtual environment.
3. Install dependencies:
   pip install -r requirements.txt
4. Use a process manager (recommended) to keep the bot running:
   - systemd service, pm2, supervisord, or Docker (not provided here).
5. Start the bot via your process manager or run:
   nohup python bot.py &

Commands and Usage
- /apply
  Starts the DM application flow. Applicant receives a DM with questions.
- /confirmation-results <application_id>
  DMs a summary of the application (applicant or staff with reviewer role).
- Staff channel interactions:
  - Pick: claim the application (only picker can score/decide)
  - Score: open modal to submit numeric score (scale 5/10/50/100; 0 allowed)
  - Approve / Deny: open modal to enter reason; requires a score first
  - View Transcript: DMs the full transcript to the requester

Behavior summary
1. Applicant runs /apply → bot DMs questions.
2. Applicant answers all questions → bot deletes its previous DM messages and leaves a single summary message with Application ID.
3. Bot posts a staff review embed in the configured staff channel.
4. Staff picks → scores → approves/denies.
5. On decision, bot deletes its DM messages (except the final result), sends a single result embed to the applicant, and stops interacting until /apply is run again.

Troubleshooting
- Bot cannot DM applicant: user has DMs disabled from server members.
- Slash commands not appearing: ensure `applications.commands` scope was used when inviting the bot; use guild-specific registration for faster sync during development.
- Bot cannot post in staff channel: check bot role permissions and channel overrides.
- If the bot restarts mid-application, in-progress state is lost (unless you enable persistence in the code).

Extending the bot
- Persist in-progress state to SQLite to survive restarts.
- Add admin commands: /set-questions, /set-staff-channel.
- Add logs channel for final application records.

Support
For issues or feature requests, contact: CodeBullet (Yoshi12345700).
