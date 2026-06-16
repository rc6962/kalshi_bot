with open("bot.log", "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()
print(f"Total lines in bot.log: {len(lines)}")
for line in lines[-150:]:
    print(line.strip())
