# Team walkthrough: the anomaly chain

This walkthrough takes every team member through the anomaly chain on the demo
recording, then through the rest of the cockpit, then through the plan for the
real files on Friday. Work through it in order. Where the screen differs from
"Expected", write down what you saw in the sign-off table (Part 8). Those notes
are what we fix before Friday.

The chain, in the order the jury will see it:

1. A measurement is abnormal, and the file shows it.
2. The chart highlights the window of the anomaly.
3. The 3D twin marks where on the part it was measured.
4. Epsilon, the copilot, explains why it was flagged, using the numbers.
5. The cockpit lists the checks to carry out. It calls them checks, not a diagnosis.
6. The incident report and the CSV come out of the same numbers.

Time: about 30 minutes for Parts 2 to 4, longer with the optional parts.

---

## Part 1: Show it to the group

1. Everyone sets up the cockpit on their own laptop (Part 2). Everyone then
   works through Part 3 alone, so each result is checked by the person who did it.
2. One person presents section 6 of `docs/DEMO_SCRIPT.md` on the projector.
3. Everyone goes through Part 3 at their own pace, in order, and fills in the
   sign-off table in Part 8.
4. Compare the tables. Where a result differs, the person who saw it says
   exactly what was on the screen.
5. Everyone runs the automatic check (Part 4).
6. Agree on the Friday plan (Part 7), and who brings which laptop and which data.

If one laptop should serve the group, start the cockpit there and open
`http://<that laptop's address>:8000/?token=<its token>` on the other laptops on
the same Wi-Fi. Find the address with `ipconfig` on Windows (the IPv4 address),
or `ifconfig` or `ip addr` on macOS and Linux. Microphone access only works on
the laptop that runs the cockpit. Partners who are not in the room need the
Cloudflare tunnel described in the README, and a strong token set first.

---

## Part 2: Set up your laptop (once)

1. Install Python 3.11 or newer from python.org, and Node.js 22.12 or newer
   (the LTS version from nodejs.org). Use Chrome or Edge.
2. Open a terminal in the project folder.
3. Run the setup:
   - Windows (PowerShell): `.\scripts\setup.ps1`
   - macOS or Linux: `./scripts/setup.sh`

   Expected: the script runs to the end without red error lines and creates
   `backend/.env`. The first run takes a few minutes.

   Then check that `backend/data/demo_recording.csv` exists. If it does not,
   create it in the `backend` folder with `.venv/bin/python scripts/make_demo_recording.py`
   (Windows: `.\.venv\Scripts\python.exe scripts\make_demo_recording.py`).
   Expected: a line that starts with "wrote" and ends with "simulated". The
   generator is seeded, so every laptop gets the same file, byte for byte.
4. Open `backend/.env` in a text editor. Replace `COCKPIT_TOKEN=change-me` with
   a password you choose, and keep it. Leave `OPENAI_API_KEY` empty unless you
   have your own key. The cockpit works without it (see Part 3, step 19).
5. Start the cockpit:
   - Windows: `.\scripts\run.ps1`
   - macOS or Linux: `./scripts/run.sh`

   Expected: the terminal shows the server running. Keep this window open for
   the whole session.
6. Open `http://localhost:8000/?token=YOUR_TOKEN` in Chrome or Edge.

   Expected: the cockpit with the title "KI Sensor Cockpit", the top bar says
   "All 2 sensors in tolerance", two sensor tiles show live readings, and a 3D
   model of the measuring setup shows a part turning under two lasers.

---

## Part 3: The anomaly chain on the demo recording

Do these steps in order. The demo recording is simulated, and the cockpit says
so in the report.

1. Click **Load file** in the top bar.

   Expected: the sheet "Load a recording" opens, with "1. Choose the file".
2. Click **Choose a file**, and select `backend/data/demo_recording.csv` from
   the project folder.

   Expected: the file name and its size appear, and "2. Check how the file was
   read" shows below.
3. Read section 2.

   Expected: columns separated by semicolons, decimals written with a comma,
   time column "column 1, s", and samples "130000 rows, 130.0 s, 1000 Hz". The
   preview shows the first rows, starting with "# Export: simulated German
   format". No warnings are listed.
4. In section 3, click the field "Part speed in rpm", type `8.6`, and press Enter.

   Expected: the field shows 8.6. Before you typed, the sheet said "No part
   speed yet, so the incidents are listed without a spot on the part." That
   line disappears.
5. Look at the limits table. Do not change anything for the demo.

   Expected: both sensors are ticked, the unit is µm, the lower limit is −40,
   the upper limit is 40, the fixture is "recording", and "Zero at start" is
   ticked. The ±40 µm band is a placeholder. The text under the table says so.
6. Scroll to section 4, and click **Analyse the recording**.

   Expected: a progress bar, then "5 incidents in 130 s of data", with the
   analysis time, which is under a second, and the kinds: vibration, drift,
   noise, invalid readings and out of tolerance.
7. Click **Show the incidents**.

   Expected: the sheet closes. The right-hand panel "Incidents" lists five rows:
   - INC-001 Vibration on Sensor 1 at 00:20.748
   - INC-002 Drift on Sensor 2 at 01:07.248
   - INC-003 Noise on Sensor 1 at 01:33.498
   - INC-004 Invalid readings on Sensor 2 at 01:45.248
   - INC-005 Vibration + Out of tolerance on Sensor 1 at 01:58.498

   The top bar button reads "Incidents (5)".
8. Click INC-001.

   Expected: the chart shows the vibration on Sensor 1, with its window shaded
   and the moment marked. The 3D twin shows a spot labelled "INC-001 measured
   here" on the part.
9. Read the incident panel for INC-001, from top to bottom.

   Expected: the title, the time, the sensor and the status "open". Then, in
   order: Checks, What the data shows, Checks to carry out, Not known from the
   file, and Where on the part, which gives an angle and a turn. A check marked
   "not found" means the rule did not find that pattern. It is not an error.
10. Click INC-002.

    Expected: the drift chart on Sensor 2, with the window shaded and a marker
    at 01:07.248 labelled "Drift". The twin spot reads "INC-002 measured here".
    The panel gives the drift rate, about 6 µm per minute, and the time to the
    limit.
11. In the INC-002 panel, click **Explain**.

    Expected: the explanation runs one step at a time. With the AI voice
    connected, Epsilon speaks, and each marker appears as its sentence starts.
    Without it, the browser voice speaks. If you hear nothing, press **M** to
    turn the sound on. The chart panel shows a step counter, such as "Step 2
    of 6", that counts up.
12. In the copilot box at the bottom right ("Ask Epsilon something"), type
    `incident 2`, and press Enter.

    Expected: INC-002 opens and is explained, as in step 11.
13. Type `incident 9`, and press Enter.

    Expected: Epsilon answers "There is no incident 9. The list has 5."
14. Click INC-003, then INC-004, then INC-005.

    Expected: each incident has its own checks and suggestions. INC-005 shows
    two kinds in its title: vibration and out of tolerance.
15. On INC-002, click **Open report**.

    Expected: a new browser tab opens with the incident report. Its sections
    are What happened, How it was found, Measurement, Checks the rules ran,
    Location, Suggested checks, and Not known from the file. For the demo
    recording, a banner says "Simulated demo data." To keep a PDF, press
    Ctrl+P (Cmd+P on a Mac), and choose "Save as PDF".
16. On INC-002, click **Download CSV**.

    Expected: the file INC-002.csv is downloaded. Its columns are separated by
    semicolons, and its decimals use commas, like the original export. German
    Excel opens it directly. In English Excel, choose Data, From Text/CSV, then
    semicolon as the separator and comma as the decimal sign.
17. On INC-002, click **Acknowledge**.

    Expected: the button disappears, and the line under the title of the panel
    ends with "acknowledged" instead of "open".
18. Press **I**, then press **I** again. Then press **Escape**.

    Expected: the incident panel hides with the first **I**, shows again with
    the second, and **Escape** closes whatever is open.
19. Optional, only with the AI voice connected: hold **Space**, ask "Investigate
    incident 2", and release.

    Expected: INC-002 opens, and Epsilon explains it. The numbers it says should
    match the INC-002 panel. Without the AI voice, skip this step.
20. Click **Load file** in the top bar, then click **Back to the simulator**.

    Expected: a message "Back to the simulator". The top bar shows "Load file"
    and "Incidents" without a number. The live sensors are back.

---

## Part 4: Check the setup automatically (1 minute)

1. Keep the cockpit running. Open a second terminal in the `backend` folder.
2. Run `.venv/bin/python scripts/chain_check.py`.
   Windows: `.\.venv\Scripts\python.exe scripts\chain_check.py`
3. Expected: 14 lines that start with PASS, and the last line "All steps
   passed."

The check loads the demo recording into the running cockpit, runs the main
steps of Part 3 through the cockpit's API, and puts the simulator back at the
end. Do not run it while you are in the middle of Part 3.

If a line says FAIL, the text after it says why. The cockpit must be running
at `http://127.0.0.1:8000`. If it reports HTTP 401, the token in `backend/.env`
does not match the cockpit. Restart the cockpit after changing the token.

---

## Part 5: The rest of the cockpit (about 10 minutes)

These steps work with the simulator, so do them after Part 3, step 20.

Demo keys. Press **D** to open the demo controls. Close them again with **D**
when you have finished.

1. Press **1**: a knock on the fixture.

   Expected: the gantry shakes, and within a few seconds an alert appears.
   The copilot reports a vibration.
2. Press **2**: drift on Sensor 2.

   Expected: after about ten seconds, a warning with the minutes until the
   limit is reached. Press **3** to stop the drift.
3. Press **4**: a noise burst.

   Expected: a noise alert appears on one of the sensors.
4. Press **5**: a dropout.

   Expected: invalid readings are flagged.
5. Press **6**: Sensor 1 gets a poor averaging setting.

   Expected: its noise rises. Type "is sensor 1 set up well?". The setup check
   opens with a proposal. Say "yes" to apply it, and the noise drops.
6. Press **7**: a dent in the part under Sensor 1.

   Expected: a red patch appears on Sensor 1's track in the twin, and it comes
   round on every turn of the part.
7. Press **0**: everything is reset.

Other keys and commands:

8. Press **Q**.

   Expected: the phone code with the address. Press **Q** again to close it.
9. Press **J**.

   Expected: mission mode, a dark full-screen view with glowing lasers. Press
   **J** again to leave it.
10. Type `status` into the copilot box.

    Expected: a short status with the numbers that are on the screen.
11. Type `how are the readings spread?`, then type `explain this chart`.

    Expected: a histogram appears. The second command walks through it step by
    step.
12. Type `show me the whole shift`.

    Expected: eight hours on one timeline, with the alerts marked. Scroll to
    zoom in.
13. Press **R**.

    Expected: the incident report of the latest live alert opens.
14. Press **M**.

    Expected: the sound switches off, and pressing **M** again switches it on.

---

## Part 6: Test the reader with other formats (optional, about 10 minutes)

The venue may give us messy exports. The generator writes the same anomalies in
other formats, so you can see how the reader copes. Write the test files to
your Desktop, not into the project. Never run the generator without `--out`,
because that overwrites the demo recording.

1. In the `backend` folder, create two test files.
   - macOS or Linux:
     `.venv/bin/python scripts/make_demo_recording.py --variant english_rate --out ~/Desktop/demo_plain.csv`
     and
     `.venv/bin/python scripts/make_demo_recording.py --variant tab_mm_counter --out ~/Desktop/demo_mm.csv`
   - Windows (PowerShell):
     `.\.venv\Scripts\python.exe scripts\make_demo_recording.py --variant english_rate --out "$HOME\Desktop\demo_plain.csv"`
     and the same with `tab_mm_counter` and `demo_mm.csv`.

   Expected: each command prints "wrote ... simulated".
2. In the cockpit, click **Load file**, and choose `demo_plain.csv`.

   Expected: the warnings say there is no time column and that the unit is
   read as µm, because the header has no unit. A field "Sample rate in Hz"
   appears. Type `1000` and press Enter. Then type `8.6` as the part speed.
3. Click **Analyse the recording**.

   Expected: the same five incidents, at the same times as in Part 3.
4. Click **Load file**, then **Back to the simulator**. Then click **Load file**
   again, and choose `demo_mm.csv`.

   Expected: the columns are separated by tabs, the decimals use commas, and
   the warnings say there is no time column and that column 1 counts rows and is
   not used as a sensor. The unit of both sensors is mm. Type `1000` as the
   sample rate and `8.6` as the part speed, then analyse.

   Expected: the same five incidents again.
5. Click **Back to the simulator**.

---

## Part 7: Friday, with the real data

We do not know yet what the venue gives us. This is the plan for the first real
file. Do not change the files in `backend/data/` in place: the cockpit reads its
own copy.

1. Ask the venue team for one or two real exports from the sensor software (CSV
   or text). Copy them into `backend/data/`.
2. Before you load a file, write down what the sheet will ask for:
   - the sensor names and models, and the tolerance limits in µm (from the drawing or the sensor sheet);
   - the part speed in rpm, or 0 if the part does not turn;
   - the sample rate, if the file has no time column;
   - the fixture or mounting of each sensor;
   - which column is which sensor.
3. Click **Load file**, choose the file, and read section 2. If the separator,
   the decimal sign, the time column or the time unit is wrong, or is marked
   "guessed", write it down before you go on.
4. Type the part speed. If it is unknown, leave 0. The incidents are still
   found, but without a spot on the part.
5. Type the real limits for each sensor. Until you do, the report says that the
   ±40 µm placeholder band was used.
6. Name the sensors and fixtures the way the team talks about them. Untick any
   column that is not a sensor.
7. Click **Analyse the recording**, and read the warnings under the result.
8. Compare the incidents with what you saw at the venue. Note any incident that
   is not real, and any real event that is missing.
9. Edit `backend/config/playbook.yaml` with the checks that the venue team wants
   to see. Each check stays a check to carry out, not a stated cause.
10. Run the automatic check on the real file, from the `backend` folder:
    `.venv/bin/python scripts/chain_check.py data/YOUR_FILE.csv --rpm SPEED`,
    and add `--rate HZ` if the file has no time column.

    Expected: all steps pass. The incident count is not checked for real files,
    because only the demo file has a known count.

Questions to take to the venue:

- Which sensors are in use, and what is their measuring range in µm?
- What are the tolerance limits, and for which part?
- At what speed does the part turn, and does it turn at all?
- Does the export have a time column? In which unit?
- Which software and which version produced the export?
- How is each sensor mounted, and what are the fixtures called?
- Are there known events to look for in the data?

---

## Part 8: Sign-off table

Fill this in for yourself. Write "yes", or describe what you saw.

| Step | What to check | Yes? | What you saw, if different |
| --- | --- | --- | --- |
| Part 2, step 6 | The cockpit opens, and shows "All 2 sensors in tolerance" | | |
| Part 3, step 1 | Load file opens the sheet | | |
| Part 3, step 2 | The demo file is chosen, and its size shows | | |
| Part 3, step 3 | Columns, decimals, time column and samples are as expected | | |
| Part 3, step 4 | The part speed is typed, and the hint disappears | | |
| Part 3, step 5 | The limits and the placeholder note | | |
| Part 3, step 6 | Five incidents in 130 s | | |
| Part 3, step 7 | Five rows, the header shows "Incidents (5)" | | |
| Part 3, step 8 | INC-001 shows its chart and the spot | | |
| Part 3, step 9 | The panel sections, in order | | |
| Part 3, step 10 | INC-002 shows the drift and the spot | | |
| Part 3, step 11 | Explain runs step by step | | |
| Part 3, step 12 | "incident 2" opens INC-002 | | |
| Part 3, step 13 | "incident 9" gives the error line | | |
| Part 3, step 14 | INC-003, INC-004 and INC-005 each have their own checks | | |
| Part 3, step 15 | The report opens with its sections | | |
| Part 3, step 16 | The CSV downloads and opens | | |
| Part 3, step 17 | Acknowledge changes the status | | |
| Part 3, step 18 | I and Escape work | | |
| Part 3, step 19 | Optional: the AI voice explains INC-002 | | |
| Part 3, step 20 | Back to the simulator clears the count | | |
| Part 4 | The automatic check ends with "All steps passed." | | |
| Part 5, steps 1 to 7 | The demo keys do what they should | | |
| Part 5, steps 8 to 14 | The other keys and commands | | |
| Part 6, steps 1 to 5 | The two test formats give the same five incidents | | |
| Part 7 | Read through, and the questions for the venue are agreed | | |

---

## Part 9: Known limits (say these out loud)

- The demo recording is simulated. The report and its banner say so. Real files
  do not carry that label.
- The analysis works at 500 Hz. The file is reduced to that rate first, so
  frequencies above 250 Hz are not visible in the analysis.
- Limits and part speed are placeholders until they are typed. The report says
  so for the limits.
- The spot on the part follows the part speed and the playback clock. It is a
  model, not a measured position of the part.
- When a file gives no part speed, the 3D part still turns at the speed in
  `backend/config/sensors.yaml` (`engine.part_rpm`, 8.6 rpm). Set that value to
  the real speed, or to 0 if the part does not turn.
- A large level step can look like vibration. The checks show whether the
  pattern is a step.
- Epsilon's numbers come from the results of the cockpit's own tools, but the
  sentences are not checked one by one. When in doubt, compare them with the
  numbers on the screen.
- The live sensor driver (MEDAQLib) is still a template. The cockpit analyses
  files now. Live sensors need the driver.
- The AI voice needs an OpenAI key and internet. Typed commands and the browser
  voice work without them.

---

## Part 10: Troubleshooting

| Problem | Fix |
| --- | --- |
| The page says "Frontend not built" | In the `frontend` folder, run `npm install`, then `npm run build`. |
| The check says the cockpit does not answer | Start the cockpit first (Part 2, step 5). |
| The check says FAIL with HTTP 401 | The token in `backend/.env` does not match the cockpit. Restart the cockpit. |
| The sheet says the mapping is not valid, with "no time column" | Type the sample rate into the field that appears. |
| There is no spot on the part | Type the part speed. 0 means unknown. |
| "No incident was found" | Check the limits and the part speed, then analyse again. |
| "An analysis is already running" | Wait until the progress bar is gone. |
| The copilot says "locked" | Click the Unlock button in the top bar, or open the cockpit with `?token=` and your token. |
| Port 8000 is busy | Change `PORT` in `backend/.env`, restart, and open the new port. |
| Excel puts the CSV into one column | Choose Data, From Text/CSV, with semicolon as the separator and comma as the decimal sign. |

---

## Part 11: Where things are

- `backend/data/demo_recording.csv`: the simulated demo recording.
- `backend/scripts/chain_check.py`: the automatic check (Part 4).
- `backend/scripts/make_demo_recording.py`: writes the test formats (Part 6).
- `backend/config/playbook.yaml`: the checks to carry out, by kind of alert.
- `backend/config/sensors.yaml`: sensor names, tolerances, part speed, and the copilot's name.
- `docs/DEMO_SCRIPT.md`: the pitch. Section 6 is the anomaly chain.
- `docs/CONTRACT.md`: the messages, the endpoints and the copilot's tools.
- `README.md`: setup and the rest of the features.
