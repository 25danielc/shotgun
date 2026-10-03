# iPhone setup: CarPlay automations and the "Shotgun" contact

This is the only Apple dependency. The phone is just a sensor: it tells the server "CarPlay connected, here's where I am." No Siri anywhere. Used by steps **1.3** (log to webhook.site) and **1.6** (real `/events`).

## What you need

- iPhone on iOS 26, the Shortcuts app, and the 2026 Civic with **wired** CarPlay.
- For step 1.3: a webhook.site URL. For 1.6: `https://<railway-domain>/events` and `EVENTS_SHARED_SECRET` from `.env`.
- **Getting the secret onto the phone without typing 48 characters:** with the Mac and iPhone on the same Apple ID and Handoff on, run this on the Mac, then long-press → Paste in the Shortcut's header field on the iPhone (Universal Clipboard). It copies without printing:
  `grep '^EVENTS_SHARED_SECRET=' .env | cut -d= -f2- | tr -d '\n' | pbcopy`
  The base URL works the same way: `grep '^PUBLIC_BASE_URL=' .env | cut -d= -f2- | tr -d '\n' | pbcopy`
- Location permission for Shortcuts: Settings → Privacy & Security → Location Services → Shortcuts → **While Using the App** (or Always).

## 1. "CarPlay connects" automation

1. Shortcuts → **Automation** tab → **+** → **CarPlay**.
2. Choose **Connects**. Choose **Run Immediately** (not "Run After Confirmation"), and turn **Notify When Run** off. TODO(verify): exact iOS 26 labels; Apple's page confirms CarPlay automations can run without confirmation.
3. **Next** → **New Blank Automation**. Add actions:
   1. **Get Current Location**.
   2. **Get Contents of URL**:
      - URL: `https://webhook.site/<id>` for step 1.3, then `https://<railway-domain>/events`.
      - Tap **Show More**. Method **POST**.
      - Headers: add `X-Shotgun-Secret` = *(EVENTS_SHARED_SECRET)* and `Content-Type` = `application/json`.
      - Request Body: **JSON** with these keys:
        - `source` (Text) = `ios_shortcut`
        - `event` (Text) = `carplay_connected`
        - `location` (Dictionary) with `lat` (Number) = *Current Location → Latitude* and `lng` (Number) = *Current Location → Longitude*. Tap the variable and pick the Latitude/Longitude property.
4. If iOS asks to allow the URL action to run automatically, allow it. Apple notes some actions must each be set to run automatically.

### Step 5.1 check: the plug-in sends its location

The ETA (D17) is computed once, from where the phone was at plug-in. Railway logged `location True` for the 18:26 plug-in, so the Shortcut already sends lat/lng (the empty location at 15:06 was an older version of it). Before the 4.1 deploy the server didn't store it; now it goes on the drive.

Check: `make watch` shows `at 42.xxxx,-83.xxxx` on the drive line. "(no location from the Shortcut)" means the Latitude/Longitude fields aren't mapped: map them to *Current Location → Latitude / Longitude* as **Number**. Without a location, the arrival call falls back to "once every job is done".

## 2. "CarPlay disconnects" automation

Same as above with **Disconnects** and `event` = `carplay_disconnected`. Location is optional. Since D17 (step 4.4) this one matters: unplugging closes the drive, cancels the arrival call if it hasn't rung, and sends the recap push. Install the **ntfy** app on the iPhone and subscribe to the `NTFY_TOPIC` from `.env` (server ntfy.sh). Copy it without printing: `grep '^NTFY_TOPIC=' .env | cut -d= -f2- | tr -d '\n' | pbcopy`.

## 3. The "Shotgun" contact (and the manual fallback)

1. Contacts → **+** → First name `Shotgun`, phone = `TWILIO_PHONE_NUMBER`. Add a photo so it's recognisable on the CarPlay screen.
2. Phone → Favorites → **+** → Shotgun.
3. **Fallback trigger (hour-4 gate):** if the automation is unreliable, tap **Shotgun** in Favorites on the CarPlay screen. Not "Hey Siri".

## 4. Step 1.3 test protocol (Daniel, in the Civic)

- Phone **locked**, replug the USB cable 5 times, about 30 s apart.
- For each replug, note the time you plugged in and the time the request landed on webhook.site, and check `location` has numbers.
- Pass: 5 of 5 logged with the phone locked, location present, latency noted. Record the results in PLAN.md (Status) and anything surprising in the DECISIONS.md log.
- Known risks (unverified reports): iOS 26 tightened background limits; wireless CarPlay adds a 5 to 15 s delay (we're wired); old iOS versions asked to unlock for "Open App" actions (we don't use that).

## 5. Pre-flight before the car (milestone 1, step 1.6)

Do these at the desk; each one is checkable without the car.

- [ ] `make smoke`: the deployed `/health` returns 200.
- [ ] `railway logs` shows `database ready` and no "not running" warnings, or only the ones you expect.
- [ ] Desk test of `/events` (rings your phone, same as plugging in):
  `curl -X POST "$URL/events" -H "X-Shotgun-Secret: $SECRET" -H 'Content-Type: application/json' -d '{"source":"desk","event":"carplay_connected","location":null}'`
  should give 202 and ring within 10 s. With a wrong secret it gives 401.
- [ ] Shortcut: run the "Connects" automation by hand (tap it in Shortcuts) and the phone rings.
- [ ] Contact "Shotgun" saved with the Twilio number and in Favorites (the fallback trigger).
- [ ] Phone: Do Not Disturb / Driving Focus off, or Shotgun allowed through, so the call actually rings.

In the Civic (parked): plug in with the phone locked → the car rings with "Shotgun" on screen within 10 s → the agent greets you. Say "thanks, bye" and it should hang up by itself. Three of three times = step 1.6.
