# Traitors

A phone-based party game for 6+ players, inspired by *The Traitors* / Werewolf. Pure Python 3 (standard library only); the app is a mobile web page, so nothing to install on phones.

## Run

```
python3 server.py [port]      # default 8000
```

Everyone on the same Wi-Fi opens the printed `http://<ip>:<port>` URL.

## Accounts

- Tap **New player**, pick a username, and you get a personal 6-digit **login code** (shown once, so write it down).
- Log in later with username + code. **Log out** whenever you like; your seat in a game stays reserved, so you can log in again (even from another phone) and carry on.
- Only the host's computer has to keep running. Accounts and games are saved to `data.json` (git-ignored) and restored when the server restarts. Set `TRAITORS_DATA` to use another file.
- After 5 wrong login attempts a username is locked for 30 seconds.

One person creates a game and shares the 4-letter room code; others join with it. The creator is the host. A player can be in one game at a time and can leave only in the lobby or after the game ends.

## Rules

- Minimum 6 players. Host sets discussion, voting and night times, number of traitors (auto = players/4) and max tempts per game.
- **Night:** traitors secretly **murder** a loyalist or **tempt** one. A tempted player can accept (becomes a traitor, nobody dies) or refuse (they are murdered).
- **Day:** discussion timer, then a voting timer. Most votes is banished and their role revealed. Ties/empty votes banish nobody.
- **Continue or stop:** after each round with traitors still alive, loyalists vote whether to continue. A majority for stop ends the game, and traitors win because one remains.
- Loyalists win when all traitors are out; traitors win when they equal or outnumber loyalists.
- The host can skip any phase (e.g. if someone's phone dies).

## Test

```
python3 test_game.py
```
