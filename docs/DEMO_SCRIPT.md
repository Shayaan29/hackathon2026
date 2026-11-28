# Pitch script: 4 minutes and 1 minute of questions

One person talks, one person drives the laptop and puts the parts under the sensor. Everything you show is the real
optoNCDT 5500 measuring real parts: there is no simulator and no recording behind it.

The story: a laser sensor that measures to a micrometre becomes a quality station that anyone on the shop floor
understands. It counts every part, flags the wrong one the moment it lands, and says what is going on.

## Before you go on stage (10 minutes earlier)

1. Sensor warm (30 minutes on), cockpit running, browser on `http://localhost:8000` in full screen (F11).
2. The tile says "Live sensor 20 kHz" and the reading is the empty plate (about 8 mm).
3. **Part check**: plate empty, **Teach the empty plate**; one good part under the laser, **Teach this part**. Take the
   part away: the tile says "Nothing under the sensor" and nothing is flagged.
4. Click the part counter in the tile, then **New run** and **Clear the part log**: the counter starts at 0 on stage.
5. Phones: `cloudflared tunnel --url http://localhost:8000` running, the address pasted under **Q** once.
6. Sound up. Five good parts and two different ones (a taller and a lower one) on the table, within reach.
7. Do not leave a part under the sensor while you wait: after a few parts the station knows the rhythm and a part that
   lies there for a long time is a "possible jam". Before the first part it says nothing.

## 0:00 The problem (30 s)

"Micro-Epsilon sensors measure to a micrometre. But on the shop floor nobody wants curves. They want to know: is this
the right part, is the line running, is something about to go wrong. Our cockpit turns one laser sensor into a quality
station that answers exactly that, and says it out loud."

Point at the tile: the reading in millimetres, the gauge with the measuring range from the data sheet.

## 0:30 Bring the jury in (20 s)

Press **Q**. "Please scan this with your phones." Wait until the top bar counts a few phones, press **Q** again.
"Your phones are now the shop floor's alarm."

## 0:50 Lights out (10 s)

Press **J**. The cockpit goes dark, the twin glows, the part counter sits on the right at 0. Say nothing for two
seconds.

## 1:00 The line runs (60 s)

Put good parts under the laser one after the other: put one down, count to two, take it away, the next one.

- "We taught it one good part and the empty plate. That is all the setup there is."
- Each part shows up green in the 3D model, and the counter goes up the moment the part leaves.
- "Every part gets a number and its height, measured to a micrometre: this one is 6.002 millimetres."
  (The last part is written under the counter.)

## 2:00 The wrong part (40 s)

Put a different part under the laser.

- Within half a second the part in the twin turns red, the alert appears, every phone in the room flashes and
  Epsilon says "Different part detected", with how much higher or lower it is than the taught part.
- "No threshold to type, no program to write: it knows the good part, so everything else is flagged."

Take it away and put a good part back: green again.

## 2:40 Ask it (40 s)

Hold **Space** (or type):

- "How many parts so far?" Epsilon gives the count, how many were good and the yield.
- "Show me the height of every part." The chart flies in: a green band for the taught part, a dot per part, the red
  dot for the foreign one.
- "Explain this chart." Epsilon walks through it and the chart points at each finding while it speaks.

"Every number it says comes from the measurement. The language model only phrases it."

## 3:20 The report (25 s)

Say "write a report" (or press **R**). The report opens: what happened, the heights of the last parts, likely causes,
next steps with the page of the manual. "Scan the code and it is on your phone. And the part log goes to Excel with
one click."

## 3:45 Close (15 s)

"One sensor, one good part taught, and the line is watched: every part counted and measured, the wrong one caught at
once, a jam, a double feed or a creeping height trend reported before it costs scrap. And with Micro-Epsilon's
MEDAQLib, any of their sensors plugs in the same way. Thank you."

## Questions you may get

| Question | Answer |
| --- | --- |
| Two different parts with the same height? | The sensor sees the height of the top surface only, so it cannot tell them apart, and we say so. A second sensor, or a profile scanner, would see the shape. |
| How does it know a part is there? | It cuts the 20 kHz signal into 50 ms frames. Half a second of something that is not the empty plate is a part; the plate showing for 0.15 s ends it. |
| How fast is the alert? | About half a second: the part must rest that long so a part that is still sliding in is not flagged. |
| Does the AI make numbers up? | No. Counts, heights and limits come from tool calls on the data. The model only phrases them, and the chart explanations are computed, not generated. |
| What is the trend warning? | A straight line through the heights of the last good parts. When it is clear and points to the edge of the band, it says in how many parts the parts will leave it. A worn tool or a fixture that moves does this. |
| More sensors? | Each sensor is a few lines in the sensor file; the cockpit, the twin and the alerts take any number. MEDAQLib finds Micro-Epsilon sensors on Ethernet and USB by itself. |
| What if the sensor fails? | The tile says what the sensor reports, for example no target or outside the measuring range, with the page of the manual, and the cockpit reconnects by itself. |

## If something fails on stage

| Problem | Do this |
| --- | --- |
| The AI voice does not connect | Type the questions in the box: the same commands work offline, Epsilon answers with the browser voice. |
| Phones cannot join | Go on without them; the alert still shows on the big screen. |
| Taking a part away raises "Different part" | The empty plate was not taught (or the plate moved): **Part check**, **Teach the empty plate**. |
| The counter does not count | Both the empty plate and the good part must be taught. |
| A "possible jam" pops up while you talk | "It noticed that a part has been lying there far longer than the parts before. On a line that is a jam." Take the part away. |
| The tile says "sensor not connected" | Check the Ethernet cable. The cockpit reconnects by itself within seconds. |
| The 3D view is blank | Leave mission mode with **J**: the tile, the counter and the charts still work. |
