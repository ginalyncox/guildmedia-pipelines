# MEC Event Standard for Replay Automation

This document defines how **Modern Events Calendar (MEC)** events must be titled and
written so the replay pipeline can automatically match Zoom recordings, link replays,
and show a **Replay available** notice on the correct calendar event.

Use this standard for every session that will be recorded and published to the Replay
Library.

---

## The golden rule

These three strings must share the same **session name**:

| System | Field | Must match |
|---|---|---|
| Zoom | Meeting topic | Primary match key |
| MEC | Event title | Must contain the Zoom topic (or vice versa) |
| Canva | Design title | Must contain the Zoom topic |

If they drift apart, the replay still publishes — but the calendar event will not link.

---

## Event title format

### Recurring series (weekly/monthly sessions)

Use a stable series name. Do **not** put the date in the MEC title for recurring events.

```
Ganjier Guild – {Series Name}
```

**Examples**

| Series | MEC event title | Zoom meeting topic |
|---|---|---|
| All Hands On Deck | `Ganjier Guild – All Hands On Deck` | `All Hands On Deck` |
| Navigator Session | `Ganjier Guild – Navigator Session` | `Navigator Session` |
| Community Education | `Ganjier Guild – Community Education` | `Community Education` |

The pipeline matches with a case-insensitive substring search. Either string can
contain the other — but keep the **core series name identical** across all three systems.

### One-time / special sessions

For single-occurrence events, include the session topic (not the calendar date) in
the title:

```
Ganjier Guild – {Session Topic}
```

**Examples**

- `Ganjier Guild – May Open Session`
- `Ganjier Guild – NECANN Debrief`

Avoid date-specific titles like `Guild Session – June 9, 2026` unless the Zoom topic
also includes that date. Dates belong in the MEC **start/end datetime**, not the title.

### Do not use in titles

- Internal codes (`AHOD-2026-W23`, `EVT-0042`)
- Account names (`jward`, `navigators`)
- Generic labels alone (`Zoom Meeting`, `Weekly Call`, `Webinar`)
- Punctuation-only differences that change the words (`All-Hands` vs `All Hands`)

---

## Zoom meeting topic (required)

Set the Zoom **meeting topic** to the series or session name **before** the recording
starts.

**Good**

- `All Hands On Deck`
- `Ganjier Guild – All Hands On Deck`
- `Navigator Session`

**Bad**

- `Gina's Zoom Meeting`
- `Personal Meeting Room`
- `Untitled`

The Zoom topic also becomes the YouTube title (with date appended), the WordPress
replay post title, and the Canva thumbnail search string.

---

## Event description template

Copy this block into every MEC event description. Replace placeholders in `{braces}`.

```html
<p><strong>{Series or Session Name}</strong><br />
{One-line tagline}</p>

<p><strong>Schedule</strong><br />
{Day pattern, e.g. Fridays} | {Start–End time} CT | Via Zoom</p>

<p><strong>Join live</strong><br />
<a href="{Zoom join URL}">Join on Zoom</a><br />
Meeting ID: {formatted meeting ID}</p>

<p><strong>About this session</strong><br />
{2–4 sentences: who it's for, what members can expect}</p>

<p><em>Replay: published automatically after the session ends. A replay link will
appear on this event page.</em></p>
```

### Required description fields

| Field | Required | Why |
|---|---|---|
| Session name (matches title) | Yes | Human clarity; backup for title matching |
| Schedule line (day + time + CT) | Yes | Members know when to attend |
| Zoom join URL | Yes | Live attendance |
| Meeting ID (with spaces) | Yes | Support + future automated matching |
| About paragraph | Yes | Public calendar quality |
| Replay note | Recommended | Sets expectation; replay link is added by pipeline |

### Example — All Hands On Deck

```html
<p><strong>All Hands On Deck</strong><br />
A Ganjier Guild weekly gathering</p>

<p><strong>Schedule</strong><br />
Fridays | 11:00 AM–12:00 PM CT | Via Zoom</p>

<p><strong>Join live</strong><br />
<a href="https://zoom.us/j/94065932895">Join on Zoom</a><br />
Meeting ID: 940 6593 2895</p>

<p><strong>About this session</strong><br />
All Hands On Deck is the Guild's weekly open session where members connect, share
updates, and collaborate. Bring questions, ideas, or just check in with the community.</p>

<p><em>Replay: published automatically after the session ends. A replay link will
appear on this event page.</em></p>
```

---

## MEC calendar settings

### Date and time

| Setting | Standard |
|---|---|
| Timezone | America/Chicago (Central Time) |
| Start / end | Actual live session window |
| Recurring series | Set recurrence in MEC; each occurrence gets its own date row |

The pipeline matches by **recording date** in the site timezone (±1 day). The MEC
event occurrence must fall on the day the session actually ran.

### Category (`mec_category`)

Assign one category per series. Keeps the public calendar filterable.

| Series | Suggested category |
|---|---|
| All Hands On Deck | `All Hands On Deck` |
| Navigator Session | `Navigators` |
| Community Education | `Community Education` |
| Certification / training | `Certification` |

### Speaker (`mec_speaker`)

Optional. Use for hosted sessions with a named facilitator; leave empty for open
community sessions.

### Featured image

Recommended. Can reuse the Canva thumbnail design for the series.

---

## Pre-flight checklist (every new series)

Before the first recording of a new session type:

- [ ] MEC event title follows `Ganjier Guild – {Series Name}`
- [ ] Zoom meeting topic contains the series name
- [ ] MEC description includes Zoom URL + Meeting ID
- [ ] MEC start/end time matches the live session (CT)
- [ ] MEC category assigned
- [ ] Canva thumbnail design title contains the series name
- [ ] Ganjier Replay Pipeline plugin active (v1.1+)
- [ ] `MEC_LINK_ENABLED=true` in pipeline `.env`

---

## Pre-flight checklist (every occurrence)

For recurring events, verify before each live session:

- [ ] Zoom meeting topic still matches the series name (not renamed ad hoc)
- [ ] MEC occurrence exists for today's date
- [ ] Canva thumbnail ready for this session (if custom per-date)

After the session:

- [ ] Check **Tools → Replay Pipeline** in WordPress for `completed` status
- [ ] Confirm **Replay available** appears on the MEC event page
- [ ] If no link: compare Zoom topic vs MEC title in the dashboard log

---

## Matching reference (for operators)

The pipeline scores title similarity between Zoom topic and MEC event title.
Minimum score: `40` (configurable via `MEC_MATCH_MIN_SCORE`).

| Zoom topic | MEC title | Result |
|---|---|---|
| `All Hands On Deck` | `Ganjier Guild – All Hands On Deck` | Match |
| `Navigator Session` | `Ganjier Guild – Navigator Session` | Match |
| `Guild Monthly Webinar` | `Ganjier Guild – All Hands On Deck` | No match |
| `zoom meeting` | `Ganjier Guild – All Hands On Deck` | No match |

---

## Naming alignment across the stack

```
MEC event title ──────┐
                      ├── same session name
Zoom meeting topic ───┤
                      │
Canva design title ───┘
        │
        ▼
  Replay pipeline
        │
        ├── YouTube upload
        ├── WordPress replay post
        └── MEC event link + "Replay available" notice
```

---

## Related docs

- [`WORKFLOW.md`](WORKFLOW.md) — full operator runbook
- [`wordpress-plugin/ganjier-replay-pipeline/README.md`](wordpress-plugin/ganjier-replay-pipeline/README.md) — plugin install
