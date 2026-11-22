# Pitch script (about 4 minutes)

One person talks, one person drives the laptop. Rehearse it twice with the
real projector and phones. Before you start: cockpit running, cloudflared
running, the tunnel address saved under **Q**, demo panel closed, sound up,
and **0** pressed in the demo panel so everything is back to normal.

## 1. The problem (30 s, clean mode)

"Micro-Epsilon sensors measure to a micrometre. But when something goes wrong
on the line, somebody has to notice it, understand it and fix it. Today that
takes an expert at a screen full of curves."

Show the cockpit: the twin of the measuring setup, two sensors live, the
copilot on the right.

## 2. Bring the jury in (30 s)

Press **Q** and show the code. "Please take out your phones and scan this.
Type your name if you like." Wait until the top bar counts the phones, then
press **Q** again to close it.

## 3. Lights out (15 s)

Press **J**. Let the room watch the lights go down and the lasers start to
glow. Say nothing for two seconds.

## 4. A judge knocks the machine (60 s)

"Dr. Weber, please shake your phone hard, three times." (Or ask them to tap
"Knock instead".)

What happens on its own:
- the gantry shakes in 3D and every phone in the room flashes
- the copilot names the judge, says it measured a 30 Hz vibration on both
  sensors at the same moment, and that this points to the fixture, not a
  broken sensor
- the spectrum flies in with the 30 Hz peak pinned

Point out: "Nothing here is scripted. The shake knocked the simulated fixture,
and the same detection that watches the real sensors found it."

## 5. The report (30 s)

Hold Space: "Write a report." The incident report opens with likely causes,
next steps and the signal around the alert. "Scan the code on the right and
the report is on your phone, as a PDF if you like."

## 6. Prediction (40 s)

Demo panel: press **D**, **2** (drift on sensor 2), close with **D**.
After about ten seconds the copilot warns: drift of about 6 µm per minute,
tolerance limit in about 7 minutes. The forecast chart shows the dashed line
hitting the limit. "The copilot sees the problem before it produces a single
bad part." Press **D**, **3** to stop the drift.

## 7. It fixes things too (50 s)

Press **D**, **6**, then **J** to leave mission mode. "Someone left sensor 1 on
the wrong settings." Hold Space: "Is sensor 1 set up well?"

The setup check opens: noise far above what the tolerance allows, the
proposal (averaging 64, measuring rate 4000 Hz so 30 Hz stays visible) and the
preview on the real last two seconds of signal. Say "yes" or click Apply. The
noise drops about five times, measured live.

## 8. Close (15 s)

"Everything you saw runs on Micro-Epsilon data: replace one YAML file with the
real sensors, or plug in MEDAQLib, and the copilot watches the real line."

## If something fails

| Problem | Do this |
| --- | --- |
| AI voice does not connect | Keep going: typed commands and the browser voice run the same script. |
| Phones cannot join | Use the hotspot of a phone for the laptop, restart cloudflared, show the new code. |
| Nobody wants to shake | Press **D**, **1** for the same knock. |
| The 3D view is blank | Gestures and charts still work; the browser needs WebGL (use Chrome or Edge). |
