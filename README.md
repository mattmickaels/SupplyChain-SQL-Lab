# SupplyChain-SQL-Lab

A local SQL learning lab that looks like a small ERP system for a fictional semiconductor equipment maker, Kestrel Process Equipment.
It runs entirely on your own laptop, with no internet needed after setup.
Every company, supplier, customer, part and number in it is made up.

## What is in it

- Five years of realistic planning data (sales orders, purchase orders, deliveries, stock, bills of materials, build bays) in a DuckDB database that is built on first start.
- Ten lessons, from tables and keys through planning analytics and timed drills.
- A SQL editor that checks each answer and shows exactly which rows are missing or extra.
- Read-only display screens (material, supplier, purchase order, sales order, BOM, where-used, stock and requirements, and bay load), each with the SQL behind it.
- A notebook of SQL and note cells, with charts, saved on this computer.
- A schema browser that shows every table, its columns, the matching SAP table and field, and the SAP-named views.
- A playground on a practice copy of the database, with a reset button.

The lab's "today" is fixed at 1 November 2026, so answers never change as real time passes.

## Desktop installer (no terminal required)

The desktop edition includes Python and all of the lab's packages.
You do not need Python, uv, Git, or an internet connection to install and run a downloaded desktop package.
Use a built installer supplied by the project maintainer; GitHub's **Code > Download ZIP** downloads source code, not the desktop app.

### Windows 11

1. Double-click **SupplyChain-SQL-Lab-Windows-Setup.exe**.
2. Follow the installation prompts, then leave **Open SupplyChain SQL Lab** checked and click **Finish**.
3. Wait for the launcher to prepare your sample data and open the lab in your browser.

For future sessions, double-click the **SupplyChain SQL Lab** desktop shortcut or find it in the Start menu.
Installation is for your Windows account and does not request administrator access.

### macOS

1. Open the disk image for your Mac: **macOS-Apple-Silicon** for an Apple chip, or **macOS-Intel** for an Intel processor.
   You can check your chip in **Apple menu > About This Mac**.
2. Drag **SupplyChain SQL Lab** into **Applications**.
3. Open **SupplyChain SQL Lab** from Applications.

### Using the launcher

The first launch builds your sample database and shows progress in a small window.
The browser opens when the lab is ready; **Open lab** brings it back if you close the browser tab.
Keep the launcher open while learning, and click **Stop lab** or close its window when you are done.
Saved lesson progress and notebooks stay on your computer, including when you upgrade or uninstall the app.
The launcher and the terminal version use the same local data folder.

Current builds are unsigned, so Windows or macOS may show an unverified-app warning or block launch under your organization's policy.
If a managed computer blocks the app, ask your IT team to approve it.
The packaging workflow checks each platform's bundled app before producing its installer download.

## Setup from source (no Python installation required)

These steps work on Windows 11 and macOS.
You do not need to install Python, Git, or a database separately.
You will install **uv**, a tool that downloads the required Python 3.12 runtime and the lab's packages for you.
You need an internet connection during setup; after setup and the first successful launch, the lab works offline.

### 1. Install uv

**Windows 11**

Open the Start menu, search for **PowerShell**, and open it.
Copy this command, paste it into PowerShell, and press Enter:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close the terminal window after installation, then open a new PowerShell window so it can find uv.

**macOS**

Open **Terminal** using Spotlight (Cmd+Space, then type Terminal).
Paste this command and press Return:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Quit Terminal after installation, then reopen it so it can find uv.

**On either system**, check that installation worked:

```text
uv --version
```

You should see a version number.
These commands come from the [official uv installation instructions](https://docs.astral.sh/uv/getting-started/installation/).

### 2. Download the lab

1. Open the [SupplyChain-SQL-Lab repository](https://github.com/mattmickaels/SupplyChain-SQL-Lab) in your browser.
   Sign in to a GitHub account with access if prompted.
2. Select the **main** branch, then click the green **Code** button and choose **Download ZIP**.
3. Extract the ZIP into a folder you want to keep.
   On Windows, right-click the ZIP and choose **Extract All**.
   On macOS, double-click the ZIP in Finder.
4. Open the extracted folder and find the folder containing both `README.md` and `pyproject.toml`.
   This is the project folder you will use below; do not run the lab from inside the ZIP.

### 3. Open a terminal in the project folder

**Windows:** In File Explorer, open the project folder containing `pyproject.toml`, right-click an empty area, and select **Open in Terminal**.
Use a PowerShell tab in that terminal.

**macOS:** In Terminal, type `cd ` (including the space), drag the project folder from Finder into Terminal, then press Return.
This changes Terminal's working folder to the lab.

### 4. Install Python and start the lab

Run these commands one at a time in that terminal:

```text
uv python install 3.12
uv run --no-dev sclab
```

The first command [installs Python through uv](https://docs.astral.sh/uv/guides/install-python/), so you do not need a separate Python installer.
The second command installs the lab's packages into its own local environment and starts the app.
The `--no-dev` option skips tools used only for developing and testing the app.
You do not need to activate an environment or run pip commands yourself.

The first launch generates the sample database, which may take a couple of minutes.
When it is ready, your browser opens the lab automatically.
If the browser does not open, copy the `http://127.0.0.1:...` address printed in the terminal into your browser.
Use the address actually printed, since the port can change if another app is using it.

Keep the terminal open while you use the lab.
To stop the app, return to the terminal and press **Ctrl+C** on either Windows or macOS.
Closing the browser tab alone does not stop the app.
Your progress is saved on your computer as you go, so you can stop and come back at any time.

### Starting again later

Open a terminal in the same project folder as described in step 3, then run:

```text
uv run --no-dev sclab
```

You only need to install uv and Python once.
Keep the extracted project folder for future launches.

### If something does not start

- **`uv` is not recognized or command not found:** Close and reopen your terminal, then try `uv --version` again.
  If it still fails, follow the troubleshooting guidance in the official uv installation instructions linked above.
- **`pyproject.toml` cannot be found:** Open the extracted project folder containing that file, then repeat step 3.
- **The `sclab` launcher is blocked:** Try `uv run --no-dev -m sclab` instead.
- **You want to open the browser yourself:** Run `uv run --no-dev sclab --no-browser`, then open the address printed in the terminal.

## How to work through it

- Begin with **Start: Databases and relationships**, a visual introduction to tables, keys, and the data behind a purchase-order screen.
- Start with lessons 1 and 2 for selecting, filtering, and sorting; lessons 3 and 4 add summaries and joins.
- Lessons 5 and 6 cover dates and missing values; lessons 7 to 10 are optional advanced practice.
- Each lesson starts with an explanation, an Excel comparison and a worked example.
- Write your answer, press Run (or Ctrl+Enter, Cmd+Enter on a Mac) to see your result, then Check to compare it with the expected answer.
- Hints go from the idea, to the clause to use, to an outline of the query; the full solution unlocks after your first Check.
- Run or Check before leaving a lesson to save your latest SQL edits.
- Use the Schema page whenever you need to know which table holds what.
- Open ERP Explorer from the top of the page to browse and search eight business areas, or type a transaction code such as MM03 in the box and press Go.
- Lessons link to the explorer in a separate browser tab so you can look up records while keeping your SQL editor open.
- The introduction begins with Kestrel's company profile, products, plants, operating flow, and the supply problem you will investigate.
- ME23N uses a classic desktop-style purchase-order workbench with selectable items, receipt history, and the SQL behind each section.
- Open the SQL workbench to query while reading, or use an exercise's **Open in workbench** button for Run, Check, and hints beside the lesson.
  Dock it, float it, drag or resize it, or minimize it without losing SQL.
  Drafts survive navigation in the same browser tab; Run or Check saves exercise SQL to the lab.
  On small screens the workbench fills the screen; Minimize returns to the lesson.
- The introduction includes an interactive plant example: change SELECT, WHERE, and GROUP BY and compare the source rows with the result.
- The schema page includes a relationship explorer built from the lab's table definitions, with links to connected tables and their keys.
- Business exercises explain the situation, the question, and how to interpret the result after Check.
  Those reflection prompts are ungraded; the interview drills retain their executive-summary task.
  Purely technical drills stay focused on SQL mechanics without an added scenario.
- The notebook is for exploring a question in steps.
- The sample notebook walks the supply story.
- From a material screen you can open its BOM, where it is used, and its stock and requirements list.
- The capacity screen is a grid, one row per bay and one column per week.
- Delete a notebook with the button on its page.
- Try anything you like in the Playground; Reset puts the practice database back.

## For developers

- Tests: `uv run pytest` (on machines that block uv's launchers, `uv run -m pytest`).
- Lint: `uv run ruff check` and `uv run ruff format --check`.
- Design and plans: `docs/superpowers/`.
- Desktop packaging and smoke checks: [packaging/README.md](packaging/README.md).
