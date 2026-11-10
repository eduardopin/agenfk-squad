# Product flows and store: onboarding, account, permissions, billing, assets

These are the flows where people drop out and where store review rejects apps.
Component measurements are in `references/mobile-patterns.md`. Store rules move,
so read the current guideline before claiming compliance and note the date you
checked. The source list at the end was carried from the upstream list dated 2026-10-23 and has not been re-checked.

## Contents

1. Onboarding
2. Sign in and create an account
3. Delete the account
4. Permissions
5. Notifications
6. Paywall and subscriptions
7. Review prompt
8. Settings and privacy
9. Widgets and Live Activities
10. Store assets
11. Forbidden deceptive patterns

## 1. Onboarding

- Let people use real content first. Ask for an account at the point it pays
  off (saving, buying, syncing) and say what they get from it.
- Keep it to three screens at most, or none. Open on the main screen with a hint
  the first time it is used instead of a feature carousel.
- Ask a personalisation question only when the answer changes what appears next,
  and always allow "Skip".
- The first-run state of each main screen is an empty state with one action (see
  the empty row in the operation-states table of
  `references/mobile-patterns.md`), not a tutorial overlay.
- If someone quits midway, bring them back to that step with their answers kept.

## 2. Sign in and create an account

| Method | Guidance |
|---|---|
| Passkey (first choice when you control the backend) | iOS: AuthenticationServices, `ASAuthorizationController`. Android: Credential Manager. Web: WebAuthn, with `autocomplete="username webauthn"` for conditional autofill. Suggest creating one right after a successful sign-in, with a single sentence on the benefit |
| Social login | An iOS app that offers a third-party login must also offer an equivalent option that limits data collection and hides the email; Sign in with Apple qualifies (App Review 4.8). Follow each provider's button rules |
| One-time code by SMS or email | Web: `autocomplete="one-time-code"`, `inputmode="numeric"`. iOS: `.oneTimeCode`. Android: SMS Retriever or User Consent; apps targeting API 37 that read the inbox may get the code delayed (Android 17 / API 37 behaviour change as of 2026-10; check the current behaviour-changes page). Use one input, not six boxes, so paste and screen readers work; show a resend countdown and a way to change the number |
| Email and password | Show the rules before typing, offer a show-password toggle, set `current-password` or `new-password` (iOS `.password`, `.newPassword`) so the password manager can fill and generate |
| Magic link | Fragile when the person switches apps on a phone; include the code in the same message |
| Biometrics | Unlock an existing session (Face ID with a usage description; Android BiometricPrompt). It is never the only proof of identity, and it always has a passcode or password fallback |

- Where account existence is sensitive, do not reveal it in errors. Give the
  next step instead: "That code expired, so we sent a new one."
- An expired session must not lose work in progress. After signing back in, the
  person lands where they were.
- While a request is pending, block resending, and tag each attempt with an id so
  a late answer from an old attempt cannot overwrite the current one.

## 3. Delete the account

- **Apple (guideline 5.1.1(v)):** if an account can be created in the app, the
  app must let people start its deletion there. Deactivation alone does not
  count. **Google Play:** an in-app path plus a web link for requesting deletion
  without reinstalling, both declared in the Play Console.
- Put it where people look: Settings, Account, Delete account. Not behind a
  support chat.
- The confirmation says what is removed, when (now or after a grace period),
  what the law makes you keep, and how to export data first. Match the friction
  to the loss; type-to-confirm only for large, irreversible loss.
- Deleting the account does not cancel a store subscription. Say so and link to
  subscription management (section 6).
- Report success only after the server confirms, with the session ended and no
  account data left in local caches.

## 4. Permissions

- Ask in context, when the person triggers the feature, never as a batch at
  launch. Show a short rationale screen first only if the reason is not obvious:
  its main button opens the system dialog and the other says "Not now". After a
  refusal, do not raise the system dialog again.
- Skip the permission when a system picker does the job: photos (PHPicker on iOS,
  Photo Picker on Android), contacts, files, and nearby devices through a
  mediated picker. Android 17 apps targeting API 37 may need `ACCESS_LOCAL_NETWORK`
  for local-network access, which a picker avoids (Android 17 / API 37 behaviour change as of 2026-10; check the current behaviour-changes page).
- Purpose strings (iOS `NS…UsageDescription`) state what the app does with the
  data in one concrete sentence, in the app's language. Boilerplate invites
  rejection.
- When denied, explain what the feature loses, keep the rest of the app working
  and offer "Open Settings" (`UIApplication.openSettingsURLString`, Android
  `Settings.ACTION_APPLICATION_DETAILS_SETTINGS`).
- App Tracking Transparency applies only if the app tracks. Keep the wording
  neutral, offer no reward and never block features for declining.
- Location: start with "while using" and approximate precision if that is
  enough; request "always" later, only for a feature that needs it.

## 5. Notifications

- Ask after the person has seen the value, for instance after placing an order
  they want to follow. Android 13 and later needs the `POST_NOTIFICATIONS`
  runtime permission. iOS offers provisional authorisation, which delivers
  quietly to Notification Centre without a prompt.
- Split by type (Android channels, which the person controls in system
  settings): transactional, reminders, marketing. Marketing needs an explicit
  opt-in that can be switched off in the app.
- Write text that is safe on a lock screen, offer a direct action where it fits
  ("Mark as delivered"), and make a tap open the exact item.
- Put frequency and quiet hours in the app's settings.

## 6. Paywall and subscriptions

- A web or PWA prototype cannot sell in-app purchases; that needs StoreKit or
  Play Billing in a native app or wrapper, and a PWA alone is not listed on the
  App Store. Simulate the store behind a module that returns an already
  formatted price, and note what is simulated.
- Beside the buy button show the plan name, price per period, billing length,
  trial length, what happens when the trial ends (the charge, its amount and
  date) and the total. Prices come localised from the store (StoreKit
  `displayPrice`, Play Billing `formattedPrice`), never with a hard-coded
  currency symbol.
- Make **Restore purchases** visible (iOS expects it for subscriptions and
  non-consumables) together with Terms and Privacy links.
- The close control is visible from the first frame. Compare annual and monthly
  plans over the same period, for example "$12.49 per month, billed $149.90
  yearly".
- Management and cancellation are one tap from Settings: iOS
  `showManageSubscriptions` or `manageSubscriptionsSheet`; Android a deep link
  to the Play subscriptions page with the product id.
- Handle real states: purchase pending (parental approval, payment in
  progress), cancelled by the person (no alarming error), failed with retry, and
  success only once the receipt or transaction is validated. Entitlements return
  after a reinstall.
- Rules for pointing people to purchases outside the app differ by store and
  country and changed in 2025–2026. Confirm the current rule before designing
  that path.

## 7. Review prompt

- Use only the system API: `requestReview` on iOS (the system caps how often it
  appears and may show nothing) and the In-App Review API on Android. Apple
  rejects a custom replacement (App Review 5.6.1).
- Trigger it after a success the person felt, not mid-task, on first launch or
  after an error. Never ask for a rating level, offer a reward, or route
  unhappy people elsewhere first.
- Since the call may show nothing, no flow waits on it.

## 8. Settings and privacy

- A grouped list of native controls. Toggles take effect at once with no Save,
  and each option explains its consequence in a line.
- Typical groups: Account, Notifications, Privacy and data, Appearance (system,
  light or dark, following the system by default), Help, About with the version,
  Sign out, Delete account.
- **Privacy law (GDPR, LGPD and similar):** consent is per purpose, not
  pre-selected, and as easy to withdraw as to give. Offer data export where
  required and keep the privacy policy reachable in the app. The store privacy
  labels must match what the app actually collects.
- Sign out asks for confirmation only when unsynced data would be lost, and
  names it.

## 9. Widgets and Live Activities

- A widget reads in about a second: one key figure, system type, no scrolling.
  Design each system size (iOS small, medium, large; the Android responsive
  grid) on its own rather than scaling one.
- A tap deep-links to the exact item. Design the no-data, loading and stale
  states, with a timestamp such as "Updated 2:30 PM".
- iOS tinted, dark and clear modes and Android dynamic colour mean artwork and
  icons must still work in one colour.
- Use a Live Activity (iOS) or an ongoing notification (Android) only for
  something that ends, like a delivery, a ride or a match. Design the compact
  and expanded layouts and end it when the event does.
- A `RemoteViews` bitmap that is too large can crash the process, and Android 17
  tightened the limit (Android 17 / API 37 behaviour change as of 2026-10; check the current behaviour-changes page). Supply images at the size they are shown.

## 10. Store assets

| Store | Requirement (confirm in the console before exporting) |
|---|---|
| App Store | 6.9-inch iPhone screenshots, 1320×2868 or 1290×2796 portrait, from which smaller iPhone classes can be scaled; 13-inch iPad, 2064×2752 or 2048×2732, if the app runs on iPad; a per-class and per-language maximum (10 at last check); PNG or JPEG, no transparency at last check. The icon is taken from the binary. A preview video is optional |
| Google Play | 2–8 screenshots per form factor, each side 320–3840 px with an aspect ratio no wider than 2:1; 1080×1920 is a good default; JPEG or 24-bit PNG with no alpha. A 1024×500 feature graphic is mandatory, as is a 512×512 icon |

- **Content:** show the real UI of the submitted build with believable data in
  the listing's language. The first two or three images carry the main task,
  since search results show them. Give each a short caption (about six words or
  fewer) stating the benefit, set in the brand font with measured contrast and
  one idea per image.
- **Risky or banned:** features that do not exist, invented ratings or awards,
  prices that may change, "download now" buttons, device frames from another
  platform, and text too small to read as a thumbnail.
- **Building them from a web app or prototype:** the target frame is 440×956
  rendered at 3x, giving 1320×2868. Capture the screen in a 390×844 viewport at
  device scale 3 once it has settled with real data. Compose it on a 440×956
  canvas from the project tokens, with the top 22–28% for the caption and the
  capture below at about 84–90% of the width, rounded, without another
  platform's frame (the bottom may run off the edge deliberately). Export PNG at
  scale 3, strip alpha, then check caption contrast and legibility at roughly
  25% size.
- **Native control language:** an emulated locale does not translate
  `<input type="time">` or `date`; they follow the browser language. Start the
  browser in the listing's language, set `<html lang>` and look at the image: a
  24-hour locale must not show "08:30 PM". On devices, take screenshots from a
  simulator of the right size or with `adb exec-out screencap -p`, and clean the
  status bar first (9:41, full battery, no notifications, via simulator
  overrides or Android demo mode).
- Localise screenshots and captions per language and leave room for text 20–30%
  longer than English.
- Feature graphic: the brand and one sentence, or nothing. Keep anything
  important well inside the edges (about 15% at last check), since Play can crop it or overlay a play
  button.

## 11. Forbidden deceptive patterns

Stores, consumer-protection law and privacy law reject these, and so does this
skill, even if asked:

- Making cancellation harder than sign-up, or only possible by chat or phone.
- Guilt-trip refusal text such as "No, I prefer paying more".
- Marketing or data-sharing boxes ticked in advance.
- Fake countdowns, fake scarcity, or a struck-through price that never applied.
- A close button that is hidden, delayed or disguised, or an ad styled as a
  system control.
- Repeating a permission or review request, or blocking features until tracking
  is accepted.
- A free trial that converts to a charge without a clear notice of amount and
  date.

If a request needs one of these, build the honest version and say why in one
line.

## Sources (carried from the upstream list dated 2026-10-23, not re-checked)

[App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
(3.1.2, 4.8, 5.1.1(v), 5.6.1) ·
[HIG](https://developer.apple.com/design/human-interface-guidelines/) ·
[AuthenticationServices](https://developer.apple.com/documentation/authenticationservices) ·
[Credential Manager](https://developer.android.com/identity/sign-in/credential-manager) ·
[Android 17 behaviour changes](https://developer.android.com/about/versions/17/behavior-changes-17) ·
[Play account deletion](https://support.google.com/googleplay/android-developer/answer/13327111) ·
[Play target API](https://developer.android.com/google/play/requirements/target-sdk) ·
[App Store Connect screenshots](https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications) ·
[Play listing graphics](https://support.google.com/googleplay/android-developer/answer/9866151) ·
[In-App Review API](https://developer.android.com/guide/playcore/in-app-review) ·
[web.dev passkey form autofill](https://web.dev/articles/passkey-form-autofill).

## Origin

Adapted from Mobile Design Studio (MIT; see NOTICE) and rewritten for this skill. Platform and store figures follow that source and are not re-verified.
