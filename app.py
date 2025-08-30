from datetime import datetime, timedelta, time
import re
import uuid
import json
import pytz
from dateutil import parser as dateparser
import streamlit as st
from streamlit.components.v1 import html
import random

# -------------------- Helpers & Config --------------------
APP_TITLE = "SafeSched — Secure Multi-Agent Scheduling Assistant"
TIMEZONE_DEFAULT = 'Asia/Kolkata'

st.set_page_config(page_title=APP_TITLE, layout='wide', initial_sidebar_state='expanded')

# Simple CSS for nicer visuals
st.markdown("""
<style>
.vg-card{background:linear-gradient(180deg,#ffffff, #f7fbff); padding:18px; border-radius:14px; box-shadow:0 6px 18px rgba(20,40,80,0.08);}
.chat-user{background:#0ea5a4;color:white;padding:10px;border-radius:12px;display:inline-block}
.chat-bot{background:#eef2ff;color:#0f172a;padding:10px;border-radius:12px;display:inline-block}
.small-muted{color:#6b7280;font-size:12px}
.slot-btn{padding:8px 10px;border-radius:8px;border:1px solid #e6eefb;margin:4px}
</style>
""", unsafe_allow_html=True)

# -------------------- Session State --------------------
if 'messages' not in st.session_state:
    st.session_state.messages = []
if 'consent_granted' not in st.session_state:
    st.session_state.consent_granted = False
if 'selected_slot' not in st.session_state:
    st.session_state.selected_slot = None
if 'bookings' not in st.session_state:
    st.session_state.bookings = []
if 'sim_calendars' not in st.session_state:
    # Simulated calendars for demo participants. Each calendar is a list of (start, end) datetimes
    tz = pytz.timezone(TIMEZONE_DEFAULT)
    now = datetime.now(tz)
    def make_busy(start_offset_hours, duration_hours):
        return (now + timedelta(hours=start_offset_hours), now + timedelta(hours=start_offset_hours+duration_hours))
    st.session_state.sim_calendars = {
        'You': [make_busy(2, 1), make_busy(26, 2)],
        'Priya': [make_busy(3, 1.5), make_busy(20, 1)],
        'Alex': [make_busy(5, 2), make_busy(28, 1)],
    }

# -------------------- Lightweight NLP Parser (Agent A) --------------------

def parse_request(text, default_tz=TIMEZONE_DEFAULT):
    """Extract participants, duration (minutes), and timeframe window from the prompt.
    This is intentionally conservative and hackathon-friendly. For production, plug an LLM.
    Returns dict: {participants:[], duration_mins:int, date_from:datetime, date_to:datetime, title:str}
    """
    tz = pytz.timezone(default_tz)
    text_lower = text.lower()

    # participants detection (look for 'with X and Y' or 'with X, Y')
    participants = []
    m = re.search(r'with ([a-z,\s]+)', text_lower)
    if m:
        ptext = m.group(1)
        ptext = ptext.replace('and', ',')
        parts = [p.strip().title() for p in ptext.split(',') if p.strip()]
        participants = parts

    # duration detection
    duration = 30 # default minutes
    md = re.search(r'(\d+)\s*(min|mins|minutes)', text_lower)
    if md:
        duration = int(md.group(1))
    else:
        mh = re.search(r'(\d+)\s*(hr|hour|hours)', text_lower)
        if mh:
            duration = int(mh.group(1)) * 60

    # timeframe detection - simple cases: 'next week', 'tomorrow', 'on DATE'
    now = datetime.now(tz)
    if 'tomorrow' in text_lower:
        date_from = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        date_to = date_from + timedelta(days=1)
    elif 'next week' in text_lower:
        # next Monday to next Sunday
        days_ahead = 7 - now.weekday()
        next_monday = (now + timedelta(days=days_ahead)).replace(hour=9, minute=0, second=0, microsecond=0)
        date_from = next_monday
        date_to = next_monday + timedelta(days=7)
    else:
        # try to parse explicit dates in text
        try:
            # dateparser may pick a time if provided
            parsed = dateparser.parse(text, fuzzy=True)
            if parsed:
                date_from = parsed.replace(hour=9, minute=0, second=0, microsecond=0)
                date_to = date_from + timedelta(days=1)
            else:
                date_from = now
                date_to = now + timedelta(days=7)
        except Exception:
            date_from = now
            date_to = now + timedelta(days=7)

    title = "Meeting"
    # subject/intent
    mtopic = re.search(r'(?:for|about|to) ([a-z\s]+)', text_lower)
    if mtopic:
        title = mtopic.group(1).strip().title()

    # If participants not found, default to a test user list
    if not participants:
        participants = ['Priya', 'Alex']

    return {
        'participants': participants,
        'duration_mins': duration,
        'date_from': date_from,
        'date_to': date_to,
        'title': title
    }

# -------------------- Calendar Agent (Agent B) - Simulated + hooks --------------------

def get_free_busy_for_participant(name, window_start, window_end):
    """Return list of busy intervals for participant inside given window. Uses simulated calendars for demo."""
    busy = st.session_state.sim_calendars.get(name, [])
    intervals = []
    for s,e in busy:
        if e < window_start or s > window_end:
            continue
        intervals.append((max(s, window_start), min(e, window_end)))
    return intervals


def compute_candidate_slots(parsed, slot_step_mins=30, work_start=9, work_end=18):
    tz = pytz.timezone(TIMEZONE_DEFAULT)
    start = parsed['date_from']
    end = parsed['date_to']
    duration = timedelta(minutes=parsed['duration_mins'])

    # align start to next slot_step
    cur = start.replace(hour=work_start, minute=0, second=0, microsecond=0)
    candidates = []
    while cur + duration <= end:
        # only inside work hours
        if cur.hour >= work_start and (cur + duration).hour <= work_end:
            # check if all participants are free
            conflict = False
            for p in parsed['participants'] + ['You']:
                busy = get_free_busy_for_participant(p, cur, cur + duration)
                if busy:
                    conflict = True
                    break
            if not conflict:
                candidates.append(cur)
        cur += timedelta(minutes=slot_step_mins)
    return candidates

# -------------------- Meeting Agent (Agent C) - create links --------------------

def create_meeting_link(preferred_provider='zoom'):
    # For demo, create a fake but realistic meeting URL
    token = uuid.uuid4().hex[:10]
    if preferred_provider == 'zoom':
        return f'https://zoom.us/j/{random.randint(1000000000,9999999999)}?pwd={token}'
    elif preferred_provider == 'google_meet':
        return f'https://meet.google.com/{token[:3]}-{token[3:6]}-{token[6:9]}'
    else:
        return f'https://calls.safesched.example/{token}'

# -------------------- Consent / Scoped Auth Simulation --------------------

def show_consent(parsed):
    st.markdown('### 🔒 Consent & Scoped Permissions')
    st.info('SafeSched requests the following limited permissions:')
    st.write('- Read free/busy information for selected participants (no event details)')
    st.write('- Create a calendar event on your behalf (with your approval)')
    st.write('- Create a conferencing link (Zoom/Google Meet) if enabled')
    agree = st.checkbox('I consent to grant these limited permissions to SafeSched for this booking')
    if agree:
        st.session_state.consent_granted = True
    return agree

# -------------------- UI Layout --------------------

# Sidebar: Project info + controls
with st.sidebar:
    st.header(APP_TITLE)
    st.caption('Secure Multi-Agent demo — Scoped consent, multi-agent orchestration, privacy-first')
    st.markdown('---')
    st.subheader('Demo Controls')
    tz = st.selectbox('Timezone', [TIMEZONE_DEFAULT, 'UTC', 'Asia/Tokyo', 'America/Los_Angeles'], index=0)
    st.write('Participants (simulated):')
    selected = st.multiselect('Pick participants (demo calendars)', list(st.session_state.sim_calendars.keys()), default=['Priya','Alex'])
    # allow user to edit simulated calendars
    if st.button('Reset Demo Calendars'):
        st.session_state.sim_calendars = st.session_state.sim_calendars
    st.markdown('---')
    st.markdown('**Pro tips:**')
    st.write('1. Type natural requests like: `Schedule a 30 min sync with Priya and Alex next week`')
    st.write('2. Use the consent checkbox to simulate Descope OAuth flow.')
    st.write('3. For production, plug Google Calendar & Zoom APIs in the connectors.')

# Main UI: Chat on left, calendar & slots on right
col1, col2 = st.columns([2,1])

with col1:
    st.markdown('<div class="vg-card">', unsafe_allow_html=True)
    st.markdown('### 💬 Ask SafeSched to schedule something')
    user_input = st.text_input('Describe the meeting:', placeholder='e.g., Schedule a 45 min interview with Priya and Alex next Thursday at afternoon')
    if st.button('Send') and user_input.strip():
        parsed = parse_request(user_input, default_tz=tz)
        st.session_state.messages.append({'role':'user','text':user_input})
        st.session_state.messages.append({'role':'assistant','text':json.dumps(parsed, default=str)})
        # Save last parsed for UI
        st.session_state.last_parsed = parsed

    # show messages
    for msg in st.session_state.messages[-8:]:
        if msg['role'] == 'user':
            st.markdown(f"<div class='chat-user'>{msg['text']}</div>", unsafe_allow_html=True)
        else:
            # pretty formatting for assistant parsed results
            try:
                data = json.loads(msg['text'])
                st.markdown("<div class='chat-bot'>Parsed Request:</div>", unsafe_allow_html=True)
                st.write(data)
            except Exception:
                st.markdown(f"<div class='chat-bot'>{msg['text']}</div>", unsafe_allow_html=True)

    # If we have a parsed request, show more flow
    if 'last_parsed' in st.session_state:
        parsed = st.session_state.last_parsed
        st.markdown('---')
        st.subheader('Parsed meeting request')
        st.write(parsed)
        st.markdown('#### 🔎 Finding candidate time slots (Agent B)')
        candidates = compute_candidate_slots(parsed)
        if not candidates:
            st.warning('No free slots found in the requested window (demo calendars). You may expand the timeframe or change duration.')
        else:
            # show candidate slots
            st.write('Candidate slots (select one to proceed):')
            for c in candidates[:12]:
                label = c.strftime('%a, %b %d — %I:%M %p')
                if st.button(f'Select {label}', key=f'slot_{c.timestamp()}'):
                    st.session_state.selected_slot = c
                    st.session_state.messages.append({'role':'assistant', 'text': f'User selected slot {label}'})

        # Consent
        st.markdown('---')
        consent = show_consent(parsed)

        # If slot selected and consent granted, show finalize button
        if st.session_state.selected_slot and st.session_state.consent_granted:
            slot = st.session_state.selected_slot
            st.success(f'Selected slot: {slot.strftime("%a, %b %d — %I:%M %p")}
')
            provider = st.selectbox('Preferred conferencing provider', ['zoom','google_meet','none'])
            if st.button('Finalize & Book'):
                # create meeting link
                link = create_meeting_link(provider)
                # create booking entry
                booking = {
                    'title': parsed.get('title','Meeting'),
                    'slot': slot.isoformat(),
                    'participants': parsed['participants'] + ['You'],
                    'link': link,
                    'created_at': datetime.now(pytz.timezone(TIMEZONE_DEFAULT)).isoformat()
                }
                st.session_state.bookings.append(booking)
                # mark slot as busy in simulated calendars
                end = slot + timedelta(minutes=parsed['duration_mins'])
                for p in parsed['participants'] + ['You']:
                    st.session_state.sim_calendars.setdefault(p,[]).append((slot,end))
                st.success('✅ Meeting booked successfully!')
                st.balloons()
                # notification area
                st.markdown('### 🔔 Notifications')
                st.write(f"Invites sent to: {', '.join(booking['participants'])}")
                st.write(f"Calendar event: {booking['title']} — {slot.strftime('%c')}")
                st.write(f"Conferencing link: {booking['link']}")

    st.markdown('</div>', unsafe_allow_html=True)

with col2:
    st.markdown('<div class="vg-card">', unsafe_allow_html=True)
    st.subheader('📆 Your Calendar (Simulated)')
    # Show a compact agenda + bookings
    tzobj = pytz.timezone(TIMEZONE_DEFAULT)
    now = datetime.now(tzobj)
    today = now.date()
    # generate a small agenda for the next 7 days
    days = [today + timedelta(days=i) for i in range(0,7)]
    for d in days:
        st.markdown(f'**{d.strftime("%A, %b %d") }**')
        # show events from simulated calendars for 'You'
        events = []
        for s,e in st.session_state.sim_calendars.get('You',[]):
            if s.date() == d:
                events.append((s,e))
        if events:
            for s,e in events:
                st.write(f'- {s.strftime("%I:%M %p")} — {e.strftime("%I:%M %p")}')
        else:
            st.write('- No events')
    st.markdown('---')
    st.subheader('Recent Bookings')
    for b in reversed(st.session_state.bookings[-6:]):
        st.markdown(f"**{b['title']}** — {parser_iso(b['slot']).strftime('%a, %I:%M %p') if b.get('slot') else 'N/A'}")
        st.write(f"Participants: {', '.join(b['participants'])}")
        st.write(f"Link: {b['link']}")
        st.markdown('---')
    st.markdown('</div>', unsafe_allow_html=True)

# -------------------- Utilities --------------------

def parser_iso(s):
    try:
        return dateparser.parse(s)
    except Exception:
        return None

# -------------------- Footer / About --------------------
st.markdown('---')
colf1, colf2 = st.columns([3,1])
with colf1:
    st.write('### About SafeSched')
    st.write('SafeSched is a hackathon-grade multi-agent demo showcasing secure, scoped consent flows and agent orchestration for everyday scheduling. For the Global MCP Hackathon, wire this demo to Descope for real OAuth and to real calendar/conferencing providers for production use.')
with colf2:
    st.write('🔗 Links & Deliverables')
    st.write('- Demo video: record a 2-3 minute flow')
    st.write('- Pitch deck: 5 slides (Problem, Solution, Architecture, Demo, Roadmap)')

# End of file
