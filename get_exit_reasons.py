with open("bot.log", "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

print(f"Searching {len(lines)} lines...")
exit_lines = []
for line in lines:
    if "[PositionManager]" in line and "EXIT" in line:
        exit_lines.append(line.strip())
    elif "realized" in line.lower() or "closed response" in line.lower():
        exit_lines.append(line.strip())

with open("exit_reasons.txt", "w") as out:
    for el in exit_lines[-100:]:
        out.write(el + "\n")

print(f"Done. Found {len(exit_lines)} exit events. Wrote last 100 to exit_reasons.txt")
