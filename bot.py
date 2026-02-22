# bot.py
# Discord staff application bot (single-file)
# Behavior: DM-based application flow; bot deletes only its own DM messages except the single final summary/result message.

import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
import configparser
import datetime
import uuid
import sys
import asyncio

# -------------------------
# Config loader (robust)
# -------------------------
cfg = configparser.ConfigParser()
cfg.read('config.ini')

def _get_str(section, option, fallback=None):
    try:
        raw = cfg.get(section, option, fallback=None)
        if raw is None:
            return fallback
        raw = raw.split(';',1)[0].split('#',1)[0].strip()
        return raw if raw != '' else fallback
    except Exception:
        return fallback

def _get_int(section, option, fallback=None):
    try:
        raw = cfg.get(section, option, fallback=None)
        if raw is None:
            return fallback
        raw = raw.split(';',1)[0].split('#',1)[0].strip()
        return int(raw) if raw != '' else fallback
    except Exception:
        return fallback

BOT_TOKEN = _get_str('bot','token')
GUILD_ID = _get_int('bot','guild_id', fallback=0) or None
STAFF_CHANNEL_ID = _get_int('bot','staff_channel_id', fallback=0)
REVIEWER_ROLE_ID = _get_int('bot','reviewer_role_id', fallback=0)
COOLDOWN = _get_int('bot','application_cooldown_seconds', fallback=300)

Q_COUNT = _get_int('questions','count', fallback=5)
QUESTIONS = []
for i in range(1, Q_COUNT+1):
    QUESTIONS.append(_get_str('questions', f"q{i}", fallback=f"Question {i}?"))

TEMPLATE_APPROVED = _get_str('templates','approved', fallback="Congrats — your application (ID {id}) has been approved. Reviewer: {reviewer}. Score: {score}/{scale}. Reason: {reason}")
TEMPLATE_DENIED = _get_str('templates','denied', fallback="We're sorry — your application (ID {id}) has been denied. Reviewer: {reviewer}. Score: {score}/{scale}. Reason: {reason}")

# -------------------------
# Database (SQLite)
# -------------------------
DB = 'applications.db'
conn = sqlite3.connect(DB, check_same_thread=False)
cur = conn.cursor()
cur.execute('''
CREATE TABLE IF NOT EXISTS applications (
    application_id TEXT PRIMARY KEY,
    user_id TEXT,
    username TEXT,
    started_at TEXT,
    finished_at TEXT,
    transcript TEXT,
    bot_message_ids TEXT,
    score INTEGER,
    score_scale INTEGER,
    decision TEXT,
    decision_reason TEXT,
    picker_id TEXT,
    reviewer_id TEXT
)
''')
conn.commit()

def new_app_id():
    return f"app_{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"

def save_app(app_id, user_id, username, transcript, bot_message_ids=None, started_at=None, finished_at=None, score=None, score_scale=None, decision=None, decision_reason=None, picker_id=None, reviewer_id=None):
    bot_ids_text = ",".join(str(x) for x in (bot_message_ids or [])) if bot_message_ids is not None else None
    cur.execute('SELECT application_id FROM applications WHERE application_id=?', (app_id,))
    if cur.fetchone():
        cur.execute('''
            UPDATE applications SET transcript=?, bot_message_ids=COALESCE(?, bot_message_ids), finished_at=COALESCE(?, finished_at),
                score=COALESCE(?, score), score_scale=COALESCE(?, score_scale), decision=COALESCE(?, decision),
                decision_reason=COALESCE(?, decision_reason), picker_id=COALESCE(?, picker_id), reviewer_id=COALESCE(?, reviewer_id)
            WHERE application_id=?
        ''', (transcript, bot_ids_text, finished_at, score, score_scale, decision, decision_reason, picker_id, reviewer_id, app_id))
    else:
        cur.execute('''
            INSERT INTO applications(application_id, user_id, username, started_at, finished_at, transcript, bot_message_ids, score, score_scale, decision, decision_reason, picker_id, reviewer_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (app_id, user_id, username, started_at, finished_at, transcript, bot_ids_text, score, score_scale, decision, decision_reason, picker_id, reviewer_id))
    conn.commit()

def fetch_app(app_id):
    cur.execute('SELECT * FROM applications WHERE application_id=?', (app_id,))
    row = cur.fetchone()
    if not row:
        return None
    keys = ['application_id','user_id','username','started_at','finished_at','transcript','bot_message_ids','score','score_scale','decision','decision_reason','picker_id','reviewer_id']
    data = dict(zip(keys, row))
    if data['bot_message_ids']:
        data['bot_message_ids'] = [int(x) for x in data['bot_message_ids'].split(',') if x.strip()]
    else:
        data['bot_message_ids'] = []
    return data

# -------------------------
# Bot setup
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)
tree = bot.tree

# in-memory ongoing DM flows: user_id -> state
ongoing = {}

# -------------------------
# Utility: delete bot messages in a user's DM but preserve exclude_ids
# -------------------------
async def delete_bot_messages_in_dm(user_id: int, recorded_ids: list, exclude_ids: list = None):
    """
    Delete bot-sent messages in the user's DM.
    - recorded_ids: list of message IDs the bot previously recorded (may include the final summary/result).
    - exclude_ids: list of message IDs to preserve (do not delete).
    The function also scans recent DM history and deletes any remaining bot messages except those in exclude_ids.
    """
    exclude_ids = set(int(x) for x in (exclude_ids or []))
    try:
        user = await bot.fetch_user(int(user_id))
    except Exception:
        return
    try:
        dm = await user.create_dm()
    except Exception:
        return
    # delete recorded bot message IDs first (skip excluded)
    for mid in (recorded_ids or []):
        try:
            if mid in exclude_ids:
                continue
            m = await dm.fetch_message(mid)
            if m and m.author == bot.user:
                await m.delete()
        except Exception:
            pass
    # scan recent DM history and delete any remaining bot messages except excluded ones
    try:
        async for m in dm.history(limit=200):
            if m.author == bot.user and m.id not in exclude_ids:
                try:
                    await m.delete()
                except Exception:
                    pass
    except Exception:
        pass

# -------------------------
# Views and Modals
# -------------------------
class ScoreModal(discord.ui.Modal, title="Submit Score"):
    scale = discord.ui.TextInput(label="Scale (5,10,50,100)", placeholder="10", required=True, max_length=4)
    score = discord.ui.TextInput(label="Score (numeric, 0 allowed)", placeholder="0", required=True, max_length=6)

    def __init__(self, app_id: str):
        super().__init__()
        self.app_id = app_id

    async def on_submit(self, interaction: discord.Interaction):
        app = fetch_app(self.app_id)
        if not app:
            await interaction.response.send_message("Application not found.", ephemeral=True)
            return
        # picker enforcement
        if app['picker_id'] and str(interaction.user.id) != str(app['picker_id']):
            await interaction.response.send_message("This application is claimed by another staff member.", ephemeral=True)
            return
        # role check if no picker
        if not app['picker_id'] and REVIEWER_ROLE_ID and isinstance(interaction.user, discord.Member):
            if not any(r.id == REVIEWER_ROLE_ID for r in interaction.user.roles):
                await interaction.response.send_message("You are not authorized to score applications.", ephemeral=True)
                return
        try:
            scale = int(self.scale.value.strip())
            score = int(self.score.value.strip())
        except ValueError:
            await interaction.response.send_message("Scale and score must be integers.", ephemeral=True)
            return
        if scale not in (5,10,50,100):
            await interaction.response.send_message("Scale must be one of: 5, 10, 50, 100.", ephemeral=True)
            return
        save_app(self.app_id, app['user_id'], app['username'], app['transcript'], bot_message_ids=app['bot_message_ids'], score=score, score_scale=scale, picker_id=app['picker_id'], reviewer_id=str(interaction.user.id))
        await interaction.response.send_message(f"Saved score {score}/{scale} for {self.app_id}", ephemeral=True)
        # update staff embed if present
        try:
            if STAFF_CHANNEL_ID:
                ch = bot.get_channel(STAFF_CHANNEL_ID)
                if ch:
                    async for msg in ch.history(limit=200):
                        if msg.author == bot.user and msg.embeds:
                            e = msg.embeds[0]
                            if e.footer and self.app_id in e.footer.text:
                                new_e = discord.Embed(title="Staff Application", color=discord.Color.gold(), timestamp=datetime.datetime.utcnow())
                                new_e.add_field(name="Applicant", value=f"{app['username']} ({app['user_id']})", inline=False)
                                new_e.add_field(name="Application ID", value=self.app_id, inline=True)
                                new_e.add_field(name="Score", value=f"{score}/{scale}", inline=True)
                                preview = app['transcript'] or "No transcript"
                                if len(preview) > 1000:
                                    preview = preview[:1000] + "..."
                                new_e.add_field(name="Transcript Preview", value=preview, inline=False)
                                new_e.set_footer(text=f"Application {self.app_id}")
                                await msg.edit(embed=new_e, view=msg.components[0] if msg.components else None)
                                break
        except Exception:
            pass

class DecisionModal(discord.ui.Modal, title="Decision Reason"):
    reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.long, required=True, max_length=1000)

    def __init__(self, app_id: str, decision: str):
        super().__init__()
        self.app_id = app_id
        self.decision = decision

    async def on_submit(self, interaction: discord.Interaction):
        app = fetch_app(self.app_id)
        if not app:
            await interaction.response.send_message("Application not found.", ephemeral=True)
            return
        # picker enforcement
        if app['picker_id'] and str(interaction.user.id) != str(app['picker_id']):
            await interaction.response.send_message("This application is claimed by another staff member.", ephemeral=True)
            return
        # require score
        if app['score'] is None:
            await interaction.response.send_message("Please score the application before making a decision.", ephemeral=True)
            return
        # save decision
        save_app(self.app_id, app['user_id'], app['username'], app['transcript'], bot_message_ids=app['bot_message_ids'], decision=self.decision, decision_reason=self.reason.value.strip(), reviewer_id=str(interaction.user.id), picker_id=app['picker_id'])
        # Build final result embed (we will preserve this message)
        final_embed = discord.Embed(
            title="Application Result",
            color=discord.Color.green() if self.decision.lower() == 'approved' else discord.Color.red(),
            timestamp=datetime.datetime.utcnow()
        )
        final_embed.add_field(name="Application ID", value=self.app_id, inline=False)
        final_embed.add_field(name="Result", value=self.decision, inline=True)
        final_embed.add_field(name="Score", value=f"{app['score']}/{app['score_scale']}" if app['score'] is not None else "N/A", inline=True)
        final_embed.add_field(name="Reviewer", value=str(interaction.user), inline=True)
        final_embed.add_field(name="Reason", value=self.reason.value.strip(), inline=False)
        final_embed.set_footer(text="Thank you for applying")
        # Delete bot messages in applicant DM but preserve the final result message (we'll send it after cleanup)
        try:
            # delete all bot messages except none for now (we'll send final after deletion)
            await delete_bot_messages_in_dm(int(app['user_id']), app.get('bot_message_ids', []), exclude_ids=[])
        except Exception:
            pass
        # Send final result and store its id so it is preserved
        try:
            user = await bot.fetch_user(int(app['user_id']))
            if user:
                sent = await user.send(embed=final_embed)
                # store final result id in DB so future cleanup won't remove it
                save_app(self.app_id, app['user_id'], app['username'], app['transcript'], bot_message_ids=[sent.id], started_at=app['started_at'], finished_at=app['finished_at'], score=app['score'], score_scale=app['score_scale'], decision=self.decision, decision_reason=self.reason.value.strip(), picker_id=app['picker_id'], reviewer_id=str(interaction.user.id))
        except Exception:
            pass
        # Update staff channel post to final compact embed and replace view with View Transcript only
        try:
            if STAFF_CHANNEL_ID:
                ch = bot.get_channel(STAFF_CHANNEL_ID)
                if ch:
                    async for msg in ch.history(limit=200):
                        if msg.author == bot.user and msg.embeds:
                            e = msg.embeds[0]
                            if e.footer and self.app_id in e.footer.text:
                                final_staff = discord.Embed(title="Staff Application (Final)", color=discord.Color.green() if self.decision.lower()=='approved' else discord.Color.red(), timestamp=datetime.datetime.utcnow())
                                final_staff.add_field(name="Applicant", value=f"{app['username']} ({app['user_id']})", inline=False)
                                final_staff.add_field(name="Application ID", value=self.app_id, inline=True)
                                final_staff.add_field(name="Status", value=self.decision, inline=True)
                                final_staff.add_field(name="Score", value=f"{app['score']}/{app['score_scale']}" if app['score'] is not None else "N/A", inline=True)
                                final_staff.add_field(name="Reviewer", value=str(interaction.user), inline=True)
                                final_staff.add_field(name="Reason", value=self.reason.value.strip(), inline=False)
                                final_staff.set_footer(text=f"Application {self.app_id}")
                                view = discord.ui.View()
                                view.add_item(discord.ui.Button(label="View Transcript", style=discord.ButtonStyle.secondary, custom_id=f"view_{self.app_id}"))
                                await msg.edit(embed=final_staff, view=view)
                                break
        except Exception:
            pass
        # Ensure no ongoing DM state remains for this user
        try:
            if str(app['user_id']) in ongoing:
                del ongoing[str(app['user_id'])]
        except Exception:
            pass
        await interaction.response.send_message(f"Decision recorded: **{self.decision}** for **{self.app_id}**", ephemeral=True)

class StaffClaimView(discord.ui.View):
    def __init__(self, app_id: str):
        super().__init__(timeout=None)
        self.app_id = app_id

    @discord.ui.button(label="Pick", style=discord.ButtonStyle.primary, custom_id="pick_button")
    async def pick(self, interaction: discord.Interaction, button: discord.ui.Button):
        app = fetch_app(self.app_id)
        if not app:
            await interaction.response.send_message("Application not found.", ephemeral=True)
            return
        if app['picker_id']:
            await interaction.response.send_message("Already claimed.", ephemeral=True)
            return
        if REVIEWER_ROLE_ID and isinstance(interaction.user, discord.Member):
            if not any(r.id == REVIEWER_ROLE_ID for r in interaction.user.roles):
                await interaction.response.send_message("You are not authorized to pick applications.", ephemeral=True)
                return
        save_app(self.app_id, app['user_id'], app['username'], app['transcript'], bot_message_ids=app['bot_message_ids'], picker_id=str(interaction.user.id))
        # update message view: show picker and enable Score/Approve/Deny
        try:
            if STAFF_CHANNEL_ID:
                ch = bot.get_channel(STAFF_CHANNEL_ID)
                if ch:
                    async for msg in ch.history(limit=200):
                        if msg.author == bot.user and msg.embeds:
                            e = msg.embeds[0]
                            if e.footer and self.app_id in e.footer.text:
                                new_view = discord.ui.View(timeout=None)
                                new_view.add_item(discord.ui.Button(label=f"Picked by {interaction.user.display_name}", style=discord.ButtonStyle.secondary, disabled=True))
                                new_view.add_item(discord.ui.Button(label="Score", style=discord.ButtonStyle.primary, custom_id=f"score_{self.app_id}"))
                                new_view.add_item(discord.ui.Button(label="Approve", style=discord.ButtonStyle.success, custom_id=f"approve_{self.app_id}"))
                                new_view.add_item(discord.ui.Button(label="Deny", style=discord.ButtonStyle.danger, custom_id=f"deny_{self.app_id}"))
                                new_view.add_item(discord.ui.Button(label="View Transcript", style=discord.ButtonStyle.secondary, custom_id=f"view_{self.app_id}"))
                                await msg.edit(view=new_view)
                                break
        except Exception:
            pass
        await interaction.response.send_message(f"You claimed {self.app_id}. You can now Score/Approve/Deny.", ephemeral=True)

# -------------------------
# Slash commands
# -------------------------
@tree.command(name="apply", description="Start a staff application", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
async def apply(interaction: discord.Interaction):
    user = interaction.user
    now = datetime.datetime.utcnow().timestamp()
    last = ongoing.get(str(user.id), {}).get('last', 0)
    if now - last < COOLDOWN:
        await interaction.response.send_message(f"Please wait before starting another application. Cooldown: {COOLDOWN}s.", ephemeral=True)
        return
    await interaction.response.send_message("Check your DMs to start the application.", ephemeral=True)
    try:
        dm = await user.create_dm()
        app_id = new_app_id()
        started = datetime.datetime.utcnow().isoformat()
        ongoing[str(user.id)] = {'app_id': app_id, 'started': started, 'q_index': 0, 'transcript': [], 'bot_message_ids': [], 'last': now}
        # welcome embed
        welcome = discord.Embed(title="Staff Application", description="Answer the questions below. Your answers are saved automatically.", color=discord.Color.blurple(), timestamp=datetime.datetime.utcnow())
        welcome.add_field(name="Application ID", value=app_id, inline=False)
        welcome.set_footer(text="Reply to each message with your answer. You can type 0 where applicable.")
        m = await dm.send(embed=welcome)
        ongoing[str(user.id)]['bot_message_ids'].append(m.id)
        # first question
        q_embed = discord.Embed(title=f"Question 1 of {len(QUESTIONS)}", description=QUESTIONS[0], color=discord.Color.dark_blue())
        q_embed.set_footer(text=f"Application {app_id}")
        qmsg = await dm.send(embed=q_embed)
        ongoing[str(user.id)]['bot_message_ids'].append(qmsg.id)
    except Exception:
        await interaction.followup.send("Couldn't DM you. Enable DMs and try again.", ephemeral=True)

@tree.command(name="confirmation-results", description="Get confirmation/results for an application", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.describe(application_id="Application ID to fetch")
async def confirmation_results(interaction: discord.Interaction, application_id: str):
    app = fetch_app(application_id)
    if not app:
        await interaction.response.send_message("Application not found.", ephemeral=True)
        return
    if str(interaction.user.id) != app['user_id'] and (REVIEWER_ROLE_ID and isinstance(interaction.user, discord.Member) and not any(r.id == REVIEWER_ROLE_ID for r in interaction.user.roles)):
        await interaction.response.send_message("You are not authorized to view this application.", ephemeral=True)
        return
    embed = discord.Embed(title=f"Application {application_id}", color=discord.Color.blue())
    embed.add_field(name="Applicant", value=f"{app['username']} ({app['user_id']})", inline=False)
    embed.add_field(name="Started", value=app['started_at'] or "N/A", inline=True)
    embed.add_field(name="Finished", value=app['finished_at'] or "N/A", inline=True)
    embed.add_field(name="Score", value=f"{app['score']}/{app['score_scale']}" if app['score'] is not None else "Not scored", inline=True)
    embed.add_field(name="Decision", value=f"{app['decision'] or 'Pending'}", inline=True)
    embed.add_field(name="Reason", value=app['decision_reason'] or "N/A", inline=False)
    transcript = app['transcript'] or "No transcript saved"
    if len(transcript) > 1900:
        transcript = transcript[:1900] + "..."
    embed.add_field(name="Transcript", value=f"```\n{transcript}\n```", inline=False)
    try:
        await interaction.user.send(embed=embed)
        await interaction.response.send_message("Sent you a DM with the application summary.", ephemeral=True)
    except Exception:
        await interaction.response.send_message("Couldn't DM you. Enable DMs.", ephemeral=True)

# -------------------------
# Interaction handler for custom_id patterns
# -------------------------
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if not interaction.data or 'custom_id' not in interaction.data:
        return
    cid = interaction.data['custom_id']
    if cid.startswith("score_"):
        app_id = cid.split("_",1)[1]
        app = fetch_app(app_id)
        if not app:
            await interaction.response.send_message("Application not found.", ephemeral=True)
            return
        if app['picker_id'] and str(interaction.user.id) != str(app['picker_id']):
            await interaction.response.send_message("Claimed by another staff member.", ephemeral=True)
            return
        modal = ScoreModal(app_id)
        await interaction.response.send_modal(modal)
    elif cid.startswith("approve_") or cid.startswith("deny_"):
        app_id = cid.split("_",1)[1]
        app = fetch_app(app_id)
        if not app:
            await interaction.response.send_message("Application not found.", ephemeral=True)
            return
        if app['picker_id'] and str(interaction.user.id) != str(app['picker_id']):
            await interaction.response.send_message("Claimed by another staff member.", ephemeral=True)
            return
        decision = "Approved" if cid.startswith("approve_") else "Denied"
        modal = DecisionModal(app_id, decision)
        await interaction.response.send_modal(modal)
    elif cid.startswith("view_"):
        app_id = cid.split("_",1)[1]
        app = fetch_app(app_id)
        if not app:
            await interaction.response.send_message("Application not found.", ephemeral=True)
            return
        if str(interaction.user.id) != app['user_id'] and (REVIEWER_ROLE_ID and isinstance(interaction.user, discord.Member) and not any(r.id == REVIEWER_ROLE_ID for r in interaction.user.roles)):
            await interaction.response.send_message("Not authorized to view.", ephemeral=True)
            return
        transcript = app['transcript'] or "No transcript saved"
        if len(transcript) > 1900:
            transcript = transcript[:1900] + "..."
        embed = discord.Embed(title=f"Transcript {app_id}", description=f"```\n{transcript}\n```", color=discord.Color.dark_grey())
        try:
            await interaction.user.send(embed=embed)
            await interaction.response.send_message("Sent transcript to your DMs.", ephemeral=True)
        except Exception:
            await interaction.response.send_message("Couldn't DM you. Enable DMs.", ephemeral=True)

# -------------------------
# DM message listener: collects answers and deletes bot messages after submission
# -------------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if isinstance(message.channel, discord.DMChannel):
        uid = str(message.author.id)
        if uid not in ongoing:
            return
        state = ongoing[uid]
        app_id = state['app_id']
        q_index = state['q_index']
        # record answer
        q_text = QUESTIONS[q_index]
        ans = message.content.strip()
        state['transcript'].append(f"Q: {q_text}\nA: {ans}\n")
        state['q_index'] += 1
        # save partial
        save_app(app_id, uid, str(message.author), "\n".join(state['transcript']), bot_message_ids=state['bot_message_ids'], started_at=state['started'])
        # next question or finish
        if state['q_index'] < len(QUESTIONS):
            next_q = QUESTIONS[state['q_index']]
            q_embed = discord.Embed(title=f"Question {state['q_index']+1} of {len(QUESTIONS)}", description=next_q, color=discord.Color.dark_blue())
            q_embed.set_footer(text=f"Application {app_id}")
            qmsg = await message.channel.send(embed=q_embed)
            state['bot_message_ids'].append(qmsg.id)
        else:
            # finished
            finished = datetime.datetime.utcnow().isoformat()
            save_app(app_id, uid, str(message.author), "\n".join(state['transcript']), bot_message_ids=state['bot_message_ids'], started_at=state['started'], finished_at=finished)
            # send single final summary embed (this is the only bot message that should remain)
            summary = discord.Embed(
                title="Application Submitted",
                description="Thank you — your application has been submitted and will be reviewed by staff.",
                color=discord.Color.green(),
                timestamp=datetime.datetime.utcnow()
            )
            summary.add_field(name="Application ID", value=app_id, inline=False)
            summary.set_footer(text="Keep this ID to check your application later.")
            summary_msg = await message.channel.send(embed=summary)
            # preserve the summary id and delete other bot messages
            try:
                # delete all recorded bot messages except the new summary_msg.id
                await delete_bot_messages_in_dm(int(uid), state['bot_message_ids'], exclude_ids=[summary_msg.id])
                # store only the summary id in DB so future cleanup preserves it
                save_app(app_id, uid, str(message.author), "\n".join(state['transcript']), bot_message_ids=[summary_msg.id], started_at=state['started'], finished_at=finished)
            except Exception:
                # if deletion fails, still store the summary id
                save_app(app_id, uid, str(message.author), "\n".join(state['transcript']), bot_message_ids=[summary_msg.id], started_at=state['started'], finished_at=finished)
            # post to staff channel
            try:
                app = fetch_app(app_id)
                staff_ch = bot.get_channel(STAFF_CHANNEL_ID) if STAFF_CHANNEL_ID else None
                embed = discord.Embed(title="New Staff Application", color=discord.Color.gold(), timestamp=datetime.datetime.utcnow())
                embed.add_field(name="Applicant", value=f"{app['username']} ({app['user_id']})", inline=False)
                embed.add_field(name="Application ID", value=app_id, inline=True)
                embed.add_field(name="Started", value=app['started_at'], inline=True)
                preview = app['transcript'] or "No transcript"
                if len(preview) > 1000:
                    preview = preview[:1000] + "..."
                embed.add_field(name="Transcript Preview", value=preview, inline=False)
                embed.set_footer(text=f"Application {app_id}")
                view = StaffClaimView(app_id)
                if staff_ch:
                    await staff_ch.send(embed=embed, view=view)
                else:
                    print("Staff channel not set in config.ini")
            except Exception:
                pass
            # cleanup: remove in-memory ongoing state so bot stops talking to user
            if uid in ongoing:
                del ongoing[uid]
    else:
        await bot.process_commands(message)

# -------------------------
# Startup
# -------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        if GUILD_ID:
            await tree.sync(guild=discord.Object(id=GUILD_ID))
        else:
            await tree.sync()
    except Exception:
        pass

# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("Set bot.token in config.ini")
        sys.exit(1)
    bot.run(BOT_TOKEN)
