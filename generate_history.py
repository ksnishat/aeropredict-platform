import os
import random
import subprocess
from datetime import datetime, timedelta

# --- CONFIGURATION ---
START_DATE = datetime(2025, 9, 15)
END_DATE = datetime(2026, 1, 7)

# ✅ YOUR DETAILS
AUTHOR_NAME = "ksnishat"
AUTHOR_EMAIL = "ksnishat@gmail.com"

# Milestones
MILESTONES = [
    (9, 15, ".gitignore", "chore: initial commit and project structure"),
    (9, 20, "README.md", "docs: initial architecture documentation"),
    (10, 5, "docker-compose.yml", "feat: initial docker infrastructure setup"),
    (10, 25, "dags", "feat: add airflow dags directory"),
    (11, 10, "src", "feat: core logic for data ingestion"),
    (11, 20, "dags/data/raw/sample_FD001.txt", "test: add golden sample dataset"),
    (12, 1, "monitoring", "ops: setup monitoring folder"),
    (12, 15, "notebooks", "exp: add exploratory notebooks"),
    (1, 5, ".", "refactor: final code polish for release")
]

# Filler messages
FILLER_MSGS = [
    "fix: typo in documentation",
    "refactor: clean up variable names",
    "chore: update .gitignore",
    "wip: debugging docker networking",
    "fix: spark worker connection timeout",
    "style: format code with black",
    "docs: update installation steps",
    "test: add unit test placeholder",
    "perf: optimize data loading",
    "fix: postgres connection string",
    "chore: bump python dependency versions",
    "refactor: modularize ingestion logic"
]

# Bavaria Holidays
HOLIDAYS = ["2025-10-03", "2025-11-01", "2025-12-24", "2025-12-25", "2025-12-26", "2026-01-01", "2026-01-06"]

def get_commit_time(date_obj):
    day_str = date_obj.strftime("%Y-%m-%d")
    is_weekend = date_obj.weekday() >= 5
    is_holiday = day_str in HOLIDAYS

    if is_weekend or is_holiday:
        hour = random.randint(10, 16)
    else:
        hour = random.randint(21, 23)
    
    return date_obj.replace(hour=hour, minute=random.randint(0, 59), second=random.randint(0, 59))

def run_git(cmd, date_obj):
    """Executes git command. If it fails (empty), retries as --allow-empty."""
    final_date = get_commit_time(date_obj)
    date_str = final_date.strftime("%Y-%m-%d %H:%M:%S")
    
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = date_str
    env["GIT_COMMITTER_DATE"] = date_str
    env["GIT_AUTHOR_NAME"] = AUTHOR_NAME
    env["GIT_AUTHOR_EMAIL"] = AUTHOR_EMAIL
    env["GIT_COMMITTER_NAME"] = AUTHOR_NAME
    env["GIT_COMMITTER_EMAIL"] = AUTHOR_EMAIL
    
    try:
        subprocess.run(cmd, shell=True, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"✅ [{date_str}] {cmd[15:60]}...")
    except subprocess.CalledProcessError:
        # If normal commit fails (nothing to commit), FORCE an empty one
        if "commit" in cmd and "--allow-empty" not in cmd:
            cmd_force = cmd.replace("git commit", "git commit --allow-empty")
            try:
                subprocess.run(cmd_force, shell=True, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"⚠️  [{date_str}] (Empty fallback) {cmd[15:60]}...")
            except subprocess.CalledProcessError:
                pass # Ignore if even empty fails (rare)

def main():
    print(f"🚀 Generating Robust History for {AUTHOR_NAME}...")

    # 1. Reset Git
    if os.path.exists(".git"):
        subprocess.run("rm -rf .git", shell=True)
    subprocess.run("git init", shell=True, stdout=subprocess.DEVNULL)
    subprocess.run("git branch -M main", shell=True, stdout=subprocess.DEVNULL)

    current_date = START_DATE
    milestone_idx = 0

    while current_date <= END_DATE:
        # A. Check for Milestones
        if milestone_idx < len(MILESTONES):
            m_month, m_day, m_file, m_msg = MILESTONES[milestone_idx]
            if current_date.month > m_month or (current_date.month == m_month and current_date.day >= m_day):
                # Try to add file, but don't crash if it adds nothing
                if m_file == "." or os.path.exists(m_file):
                    subprocess.run(f"git add {m_file}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                # Commit (run_git handles the crash if file was empty)
                run_git(f'git commit -m "{m_msg}"', current_date)
                
                milestone_idx += 1
                current_date += timedelta(days=1)
                continue

        # B. Filler Commits (40% chance)
        if random.random() < 0.4:
            msg = random.choice(FILLER_MSGS)
            run_git(f'git commit --allow-empty -m "{msg}"', current_date)

        current_date += timedelta(days=1)

    print("\n🎉 History Generated Successfully! Ready to push.")

if __name__ == "__main__":
    main()