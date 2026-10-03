# iPhone setup: CarPlay automations and the "Shotgun" contact

This is the only Apple dependency. The phone is just a sensor: it tells the server "CarPlay connected, here's where I am." No Siri anywhere. Used by steps **1.3** (log to webhook.site) and **1.6** (real `/events`).

## What you need

- iPhone on iOS 26, the Shortcuts app, and the 2026 Civic with **wired** CarPlay.
- For step 1.3: a webhook.site URL. For 1.6: `https://<railway-domain>/events` and `EVENTS_SHARED_SECRET` from `.env`.
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

## 2. "CarPlay disconnects" automation

Same as above with **Disconnects** and `event` = `carplay_disconnected`. Location is optional.

## 3. The "Shotgun" contact (and the manual fallback)

1. Contacts → **+** → First name `Shotgun`, phone = `TWILIO_PHONE_NUMBER`. Add a photo so it's recognisable on the CarPlay screen.
2. Phone → Favorites → **+** → Shotgun.
3. **Fallback trigger (hour-4 gate):** if the automation is unreliable, tap **Shotgun** in Favorites on the CarPlay screen. Not "Hey Siri".

## 4. Step 1.3 test protocol (Daniel, in the Civic)

- Phone **locked**, replug the USB cable 5 times, about 30 s apart.
- For each replug, note the time you plugged in and the time the request landed on webhook.site, and check `location` has numbers.
- Pass: 5 of 5 logged with the phone locked, location present, latency noted. Record the results in PLAN.md (Status) and anything surprising in the DECISIONS.md log.
- Known risks (unverified reports): iOS 26 tightened background limits; wireless CarPlay adds a 5 to 15 s delay (we're wired); old iOS versions asked to unlock for "Open App" actions (we don't use that).
