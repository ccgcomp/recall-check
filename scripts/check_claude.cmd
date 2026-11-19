@echo off
rem Checks that Claude Code can be called from a script using your Claude login.
rem Sends one short FDA recall code field to Claude and prints the JSON it returns.
claude -p "Extract product codes, lot codes and dates from this FDA recall code field. Return JSON only, and include the exact source text for each item: FG-M1MOT-UW  Best by Nov 1, 2016."
