# Demo script

About 90 seconds. Adapt sensor names to what Micro-Epsilon hands you.

## Before you go on stage

1. Backend running, cockpit open full screen (F11) on the projector at `http://localhost:8000`.
2. AI voice connected (green "Voice AI" pill) or the offline mode tested.
3. Tunnel running, QR address pasted (press **Q** to check), phone counter working.
4. Sound through the room speaker, push-to-talk mic in hand.
5. Press **D**, then **0** to reset the simulator. Close the demo panel.

## On stage

1. **Hook.** "Today a sensor problem needs an expert. With our copilot the operator solves it in 30 seconds."
2. **Phones.** Press **Q**: "Scan this and tap Join." Wait for the counter, press **Esc**.
3. **Show me.** Hold Space: "Show me the last five minutes of sensor one." The chart flies in.
4. **The tap.** A teammate taps the table (or **D**, **1**). The cockpit flashes and chimes, the copilot speaks up on its own, and every judge's phone turns orange.
5. **Diagnose.** "Is the sensor broken?" The spectrum flies in: one clean 30 Hz peak, so mechanical, not the sensor.
6. **Confirm.** "Compare the sensors." Both saw it at the same moment, so the fixture moved.
7. **Back.** "Go back." The chart flies back into the stack and the spectrum returns.
8. **Predict.** Started a minute earlier with **D**, **2**: the drift alert. "Show me the drift." The forecast line shows when sensor two leaves tolerance.
9. **Act.** "Set averaging on sensor one to 64." It asks; "Yes." The noise visibly drops.
10. **Close.** "Every number you heard came from the sensor data, not from the AI. The AI only explains it."

## If something fails

* Voice API down: type the same commands in the bottom bar; the offline voice answers.
* Sensor connection down: set `SOURCE_OVERRIDE=simulator` and restart, or keep going, since the failed sensor already fell back to the simulator.
* Internet down: phones won't work; skip step 2 and show one phone on the projector instead.
* Everything down: play the backup video.

## Likely jury questions

* **How fast is an alert?** Under a second from the tap to the banner; the phones a moment later.
* **What does it cost?** The analysis runs locally. Only the voice uses a paid API: roughly ten cents per minute of conversation with the full model, less with the mini model.
* **What if the AI is wrong?** It can't invent measurements: every number comes from tools, and setting changes need an explicit yes.
* **Data privacy?** Raw data never leaves the laptop. The voice model only receives short summaries.
