with open("bot.log", "r", encoding="utf-8") as f:
    for line in f:
        if "Parsed event target strike:" in line:
            print(line.strip())
        if "Selected" in line and "market" in line:
            print(line.strip())
        if "Strike:" in line:
            print(line.strip())
        if "Ticker:" in line:
            print(line.strip())
