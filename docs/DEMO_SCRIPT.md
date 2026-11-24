# Pitch script (about 5 minutes)

One person talks, one person drives the laptop. Rehearse it twice with the
real projector and phones. Before you start: cockpit running, cloudflared
running, the tunnel address saved under **Q**, demo panel closed, sound up,
and **0** pressed in the demo panel so everything is back to normal. The day
before, run `backend/.venv/bin/python scripts/chain_check.py`: it must end with
"All steps passed." That proves section 6 will work on this laptop.

The challenge is a user interface for sensor data, so the story is: the
cockpit shows the right picture at the right moment, and explains it.
Sections 8 and 10 are optional; drop them if you are short on time.

## 1. The problem (30 s, clean mode)

"Micro-Epsilon sensors measure to a micrometre. But a screen full of curves
does not tell you what is going on. Our cockpit picks the right picture for
the question, points at what matters, and says it out loud."

Show the cockpit: the twin of the measuring setup, the part turning under the
two lasers, each laser painting its track green. "Every reading is painted
where it was taken on the part. Green means in tolerance."

## 2. Bring the jury in (30 s)

Press **Q** and show the code. "Please take out your phones and scan this.
Type your name if you like." Wait until the top bar counts the phones, then
press **Q** again to close it.

## 3. Lights out (15 s)

Press **J**. Let the room watch the lights go down and the lasers start to
glow. Say nothing for two seconds.

## 4. A judge knocks the machine (70 s)

"Dr. Weber, please shake your phone hard, three times." (Or ask them to tap
"Knock instead".)

What happens on its own:
- the gantry shakes in 3D and every phone in the room flashes
- the copilot names the judge and says it measured a 30 Hz vibration on both
  sensors at the same moment
- the spectrum flies in with the 30 Hz peak pinned

Then hold Space: "Do the sensors move together?" A scatter plot flies in: a
round cloud and one bright streak. Hold Space: "Explain this chart." While the
copilot talks, the chart points along: first the cloud ("apart from the knock
the sensors move independently, that is just noise"), then the streak ("during
the knock they moved together, correlation 0.97: one push moved both, so it
came through the fixture, not from one sensor").

Point out: "Nothing here is scripted. The shake knocked the simulated fixture,
the same detection that watches the real sensors found it, and every number
in that explanation was computed from the data."

## 5. Where on the part (40 s)

Press **D**, **7** (a dent in the part under sensor 1), close with **D**.
Within a few seconds the dent comes round: a red patch appears on sensor 1's
track, the copilot reports out of tolerance and the live signal flies in with
the spike over the limit.

Say "dismiss" (or press **Delete**) so the chart turns to dust. "The chart
tells you when. The twin shows you where: the red patch comes round on every
turn, so this is the part, not the sensor." Press **D**, **0** to take the dent
away.

## 6. The anomaly chain (90 s)

Before this section, load the demo recording: **Load file**, choose
`backend/data/demo_recording.csv`, type `8.6` as the part speed, click
**Analyse the recording**, then **Show the incidents**. It takes about 15
seconds, so say the next line while it runs.

"One abnormal measurement, followed through the whole cockpit."

1. Click **INC-002**, the drift on sensor 2. "The measurement graph highlights
   the anomaly: the shaded part is the window where the drift was seen, and the
   marker is where the alert fires."
2. Point at the twin, at the label "INC-002 measured here". "The 3D component
   shows where on the part this measurement was taken."
3. Click **Explain**. "Epsilon explains why the anomaly was flagged, one step at
   a time: the drift rate, the straight-line fit, the time to the limit. Each
   marker appears as it is spoken."
4. Point at **Checks to carry out**. "The system recommends checks, and it calls
   them checks to carry out, not a diagnosis."
5. Click **Open report**. "A complete incident report, from the same numbers.
   Every number on the screen was computed from the file."

Then **Load file**, **Back to the simulator**, and carry on.

## 7. The whole shift (50 s)

Hold Space: "What happened today?" Eight hours on one timeline, every alert
marked. "Walk me through it." The copilot flies to each incident in turn: an
earlier knock, zoomed down to every single reading, 2 ms apart; the
noisy stretch; the moment the signal was lost; then back to the last ten
minutes.

"From eight hours to two milliseconds in one motion, and no spike gets lost
on the way: the band around the line always keeps the highest and lowest
reading."

## 8. Prediction (40 s, optional)

Press **D**, **2** (drift on sensor 2), close with **D**. After about ten
seconds the copilot warns: drift of about 6 µm per minute, tolerance limit in
about 7 minutes. The forecast chart shows the dashed line hitting the limit.
"The copilot sees the problem before it produces a single bad part." Press
**D**, **3** to stop the drift.

## 9. The report (30 s)

Hold Space: "Write a report." The incident report opens with likely causes,
next steps and the signal around the alert. "Scan the code on the right and
the report is on your phone, as a PDF if you like."

## 10. It fixes things too (50 s, optional)

Press **D**, **6**, then **J** to leave mission mode. "Someone left sensor 1 on
the wrong settings." Hold Space: "Is sensor 1 set up well?"

The setup check opens: noise far above what the tolerance allows, the
proposal (averaging 64, measuring rate 4000 Hz so 30 Hz stays visible) and the
preview on the real last two seconds of signal. Say "yes" or click Apply. The
noise drops about five times, measured live.

## 11. Close (15 s)

"Everything you saw runs on Micro-Epsilon data: replace one YAML file with the
real sensors, or plug in MEDAQLib, and the cockpit watches the real line. The
charts, the explanations and the timeline come from the data, so they work
the same on day one."

## If something fails

| Problem | Do this |
| --- | --- |
| AI voice does not connect | Keep going: typed commands and the browser voice run the same script, including "explain this chart". |
| Phones cannot join | Use the hotspot of a phone for the laptop, restart cloudflared, show the new code. |
| Nobody wants to shake | Press **D**, **1** for the same knock. |
| A chart hides the part | Say "dismiss" or press **Delete**. |
| The recording does not load on stage | Press **Escape**, skip section 6, and go on with section 7. Sections 7 onward use the simulator. |
| The incident spot is missing on the part | The part speed was not typed. Type `8.6` in the sheet, then analyse again. |
| The scatter has no bright streak | The knock is older than a minute: knock again, then ask. |
| The 3D view is blank | Gestures and charts still work; the browser needs WebGL (use Chrome or Edge). |
