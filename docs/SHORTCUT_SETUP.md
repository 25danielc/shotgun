# iPhone setup: CarPlay automations and the "Shotgun" contact

This is the only Apple dependency. The phone is just a sensor: it tells the server "CarPlay connected, here's where I am." No Siri anywhere.

## What you need

- An iPhone with the Shortcuts app, and a car with **wired** CarPlay (wireless CarPlay adds a 5 to 15 s delay).
- `https://<your-server>/events` and `EVENTS_SHARED_SECRET` from `.env`.
- **Getting the secret onto the phone without typing 48 characters:** with the Mac and iPhone on the same Apple ID and Handoff on, run this on the Mac, then long-press → Paste in the Shortcut's header field on the iPhone (Universal Clipboard). It copies without printing:
  `grep '^EVENTS_SHARED_SECRET=' .env | cut -d= -f2- | tr -d '\n' | pbcopy`
  The base URL works the same way: `grep '^PUBLIC_BASE_URL=' .env | cut -d= -f2- | tr -d '\n' | pbcopy`
- Location permission for Shortcuts: Settings → Privacy & Security → Location Services → Shortcuts → **While Using the App** (or Always).

## 1. "CarPlay connects" automation

1. Shortcuts → **Automation** tab → **+** → **CarPlay**.
2. Choose **Connects**. Choose **Run Immediately** (not "Run After Confirmation"), and turn **Notify When Run** off.
3. **Next** → **New Blank Automation**. Add actions:
   1. **Get Current Location**.
   2. **Get Contents of URL**:
      - URL: `https://<your-server>/events`.
      - Tap **Show More**. Method **POST**.
      - Headers: add `X-Shotgun-Secret` = *(EVENTS_SHARED_SECRET)* and `Content-Type` = `application/json`.
      - Request Body: **JSON** with these keys:
        - `source` (Text) = `ios_shortcut`
        - `event` (Text) = `carplay_connected`
        - `location` (Dictionary) with `lat` (Number) = *Current Location → Latitude* and `lng` (Number) = *Current Location → Longitude*. Tap the variable and pick the Latitude/Longitude property.
4. If iOS asks to allow the URL action to run automatically, allow it.

The location sets the ETA, and the arrival call rings about 3 minutes before you get there. Without it, the arrival call waits until every job is done. If `make watch` shows "(no location from the Shortcut)", the Latitude/Longitude fields aren't mapped as **Number**.

## 2. "CarPlay disconnects" automation

Same as above with **Disconnects** and `event` = `carplay_disconnected`. Location is optional. Unplugging closes the drive, cancels the arrival call if it hasn't rung, and sends the recap push. Install the **ntfy** app on the iPhone and subscribe to the `NTFY_TOPIC` from `.env` (server ntfy.sh). Copy it without printing: `grep '^NTFY_TOPIC=' .env | cut -d= -f2- | tr -d '\n' | pbcopy`.

## 3. The "Shotgun" contact (and the manual fallback)

1. Contacts → **+** → First name `Shotgun`, phone = `TWILIO_PHONE_NUMBER`. Add a photo so it's recognisable on the CarPlay screen.
2. Phone → Favorites → **+** → Shotgun.
3. **Fallback trigger:** if the automation doesn't fire, tap **Shotgun** in Favorites on the CarPlay screen.

## 4. Check it before you drive

- [ ] `make smoke`: the deployed `/health` returns 200.
- [ ] Desk test of `/events` (rings your phone, same as plugging in):
  `curl -X POST "$URL/events" -H "X-Shotgun-Secret: $SECRET" -H 'Content-Type: application/json' -d '{"source":"desk","event":"carplay_connected","location":null}'`
  should give 202 and ring within 10 s. With a wrong secret it gives 401.
- [ ] Run the "Connects" automation by hand (tap it in Shortcuts) and the phone rings.
- [ ] Do Not Disturb / Driving Focus is off, or Shotgun is allowed through, so the call actually rings.

In the car (parked): plug in with the phone locked → the car rings with "Shotgun" on screen within 10 s → the agent greets you. Say "thanks, bye" and it hangs up.
