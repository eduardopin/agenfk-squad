# Mobile component patterns

Use this file one unit at a time: open the section for the component you are
building and skip the rest. A system or design-system component beats a custom
one, because it already carries accessibility, gestures and the look of the OS
version (Liquid Glass on iOS 26 and later, Material 3 and its Expressive update
on Android). Treat the numbers as starting points and check them against the
platform version the project targets.

## Reference measurements

| Element | iOS (pt) | Android, Material 3 (dp) | Mobile web (CSS px) |
|---|---|---|---|
| Touch target | 44×44 | 48×48 (Compose pads small controls to this by default) | 24×24 is the WCAG 2.5.8 floor; aim for 44–48 |
| Body text | 17, scales with Dynamic Type | 16 or 14 sp, scales with font size | 16 (1 rem) |
| Bottom navigation | System tab bar, height not yours to set | 64–80, three to five destinations | Measured height plus the bottom safe-area inset |
| Top bar | System navigation bar, large title on root lists | 64 | 56–64 |
| Button | System control height | 40 drawn, 48 tappable | 44–48 tall at least |
| Text field | System control height | 56 | 48 tall at least |
| List row | 44 minimum | 56, 72 or 88 for one, two or three lines | 48–56 |
| Floating action button | not used | 56 (40 small, 96 large) | 48 or more |
| Screen side margin | System layout margins, roughly 16–20 | 16 | 16 |

Sources: [Apple HIG](https://developer.apple.com/design/human-interface-guidelines/),
[Material 3](https://m3.material.io/),
[WCAG 2.5.8](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html).

## Target size

Three different numbers get quoted for the same thing, and they are different
kinds of rule. Name which one a finding is about.

- **WCAG 2.2 level AA, success criterion 2.5.8 (target size, minimum):** a
  pointer target measures at least 24×24 CSS px unless an exception applies.
  The main exception is spacing: a smaller target passes when a 24 px circle
  centred on it does not overlap any neighbouring target or its circle. The
  others are an equivalent control elsewhere on the page that does meet the
  size, a link inside a sentence, a size fixed by the browser itself, and a
  case where the presentation is essential. A target under 24×24 with no
  exception is a **fail**.
- **WCAG 2.5.5 (level AAA, target size enhanced):** 44×44 CSS px, with its own
  exceptions. It is a higher goal, not the AA floor.
- **Platform guidance:** 44×44 pt on iOS and 48×48 dp on Android. These are
  design recommendations in native units, not WCAG criteria.
- **House comfort target** for a new standalone web control: 44–48 CSS px, with
  about 8 px between separate actions. A target between 24 and that comfort
  size is a **warn**: inspect it in the capture and write down the decision.
  It is not a WCAG failure, and 48 px is not a universal WCAG requirement.

What follows from this:

- Size controls with `min-block-size` and `min-inline-size`, not a fixed height,
  so long labels, translations and enlarged text still fit. A small icon can
  keep a big hit area: extend it (`hitSlop` in React Native, `contentShape` in
  SwiftUI, a padded tap target in Flutter).
- The whole row of a list is the target. A link inside running text is judged
  by the inline exception, not as a square button.
- Measure the box after layout, never the value written in the stylesheet.

## Global navigation

- **Use it** for three to five top-level destinations people return to often.
  Not for the steps of one flow, and not for actions such as "New".
- **Each item:** an icon with a label that is always shown. Mark the active item
  by shape or indicator as well as colour. Badges stay short.
- **Behaviour:** selecting the current item scrolls to the top or pops to its
  root, and every destination keeps its own state. Hide the bar on focused task
  screens such as checkout or full-screen editing.
- **iOS 26 and later:** the system bar floats and can shrink while scrolling, so
  use it as is and do not paint a background behind it. **Web:** reserve room
  equal to the bar's real height plus `env(safe-area-inset-bottom)` so it never
  hides the end of the content.
- **Accessibility:** a `nav` landmark or tab-list role, `aria-current="page"` on
  the web, and accessible names that match the visible labels.

## Top bar and title

- Show a back control on the leading side (web, Android) and rely on the edge
  swipe on iOS. The title is short and says where the person is.
- At most two icon actions, then an overflow menu. The main action of the
  screen belongs in the content, not buried in the bar.
- iOS root lists use a large title that collapses on scroll. A fixed web bar
  must not cover anchor targets or the focused element: set
  `scroll-padding-top`.

## Lists and rows

- The entire row is tappable and at least 48–56 tall. Primary text above
  secondary text, with the secondary text still at 4.5:1 or better. Draw a
  chevron only when the row navigates.
- A swipe action always has a visible equivalent, such as a menu or a button on
  the detail screen. A destructive action offers undo or asks first.
- Long lists are virtualised (FlatList or FlashList, `ListView.builder`,
  `LazyColumn`, SwiftUI `List`) and load in pages, with end-of-list and error
  states in the footer.
- Pull to refresh only where the data changes, and keep the current content on
  screen while it runs.

## Cards

- Cards suit mixed items that open as a unit, such as a product or an event.
  A plain list of similar rows does not need them.
- One main target per card: the card itself or one button. Secondary actions
  are explicit and at least 8 apart.
- Never nest a card in a card, and use a light shadow or a border, not both.

## Forms

- One column. A visible label sits above each field and short help text below
  it. A placeholder gives an example and never replaces the label.
- Ask for the right keyboard and autofill. Web: `type`, `inputmode`,
  `autocomplete`, `enterkeyhint`. React Native: `keyboardType`,
  `textContentType` or `autoComplete`, `returnKeyType`. Flutter: `keyboardType`,
  `autofillHints`, `textInputAction`. Compose: `KeyboardOptions`.
- Validate when the person leaves a field and on submit, not while they first
  type. Put the error under the field with an icon and text, link it to the
  field (`aria-describedby`, `errorText`, `supportingText`), say how to fix it
  and keep what was typed. A message appearing on blur must not shift the
  submit button between press and release, or the tap lands on nothing.
- In a long form add a summary at the top that moves focus to the first invalid
  field, and show progress such as "Step 2 of 3".
- The submit button sits at the end, spans the width on a phone and stays
  enabled; say what is missing rather than greying it out. While sending, change
  the label to "Sending…", block a second submit and keep the width fixed.
- With the keyboard open, the focused field and its message stay visible, the
  "next" key moves on, and the main action is not hidden under the keyboard
  (`KeyboardAvoidingView` or a keyboard-controller library, `imePadding()` in
  Compose, `resizeToAvoidBottomInset` in Flutter).
- Passwords get a show toggle. One-time codes use `autocomplete="one-time-code"`
  or `textContentType=oneTimeCode`.

## Buttons and action hierarchy

- One primary action per context: filled, in the accent colour. Secondary is
  tonal or outlined, tertiary is plain text.
- Name the action with a verb and its object, as in "Confirm order". An icon
  supports the label rather than replacing it, except for universal glyphs.
- A destructive action uses the danger colour and a confirmation sized to the
  damage, or an undo window of a few seconds.
- In a dialog or sheet with two buttons, the primary goes on the right on
  Android and web and follows the system on iOS. Two buttons never carry the
  same visual weight.

## Bottom sheets, modals and dialogs

- A sheet fits short tasks tied to the current screen, such as filters or
  options. iOS: `presentationDetents` or the system sheet. Android:
  `ModalBottomSheet`. Web: `<dialog>` with focus trapped inside.
- Show a drag handle and a labelled Close button. Closing must not discard
  typed input without a warning, and sheets are not stacked.
- A long task gets its own screen with a back control, not a very tall sheet.
- A dialog is for a decision that blocks. Its title is a question and its
  buttons are verbs such as "Delete" and "Keep".
- Focus moves into the content when it opens and back to the trigger when it
  closes. Escape or the back gesture dismisses it.

## Search and filters

- On a large list or catalogue keep search visible at the top. On iOS 26 the
  system component can sit in the bottom bar.
- Pick one behaviour, results as the person types (debounced) or on submit, and
  keep it everywhere. Returning to the screen restores the query and filters.
- A search with no results repeats the term, offers a likely correction and a
  "Clear filters" action. Active filters show as removable chips next to a
  result count.

## Operation states

Cover only the states the operation can reach, and mark the others as not
applicable.

| State | Show | Avoid |
|---|---|---|
| First load | A skeleton shaped like the final layout, one spoken announcement | An unbounded full-screen spinner, or announcing every placeholder |
| Refresh | The old content plus a quiet progress cue | Clearing the list first |
| Empty | The reason, the next step, optionally an illustration | A blank page or a bare "No data" |
| Recoverable error | What failed, what to do, a retry control, input kept | Raw error codes, losing a filled form |
| Offline | A persistent notice; queue actions only if the app really does | Implying a sync that does not exist |
| Success | The actual outcome (number, date, item) and what comes next | Celebrating before the server confirms |
| Permission denied | The effect on this feature and a way to Settings | Re-asking in a loop |

## Transient feedback

- Snackbars and toasts suit light confirmations and **undo**. Keep them up for
  4–10 seconds and announce them to assistive tech (`role="status"`,
  `announceForAccessibility`, `AccessibilityNotification`).
- Do not use them for an error that needs action. They must not cover the
  bottom bar or the main action, and only one shows at a time.

## Onboarding and permissions

- Demonstrate value before asking for an account or a permission, and let people
  skip and look around.
- Request a permission when the action that needs it happens, preceded by a
  short explanation if the reason is not obvious.
- Do not ask for everything on first launch. The complete treatment lives in
  `references/product-flows-and-store.md`.

## Gestures

- Every gesture (swipe, long-press, pinch, drag) has a visible alternative that
  works with assistive technology.
- Stay clear of system back: the left screen edge on iOS, and predictive back on
  Android, which is on by default for apps targeting API 36.
- Reordering by drag has a visible handle and "Move up" and "Move down" actions.

## Tablet, foldable and landscape

- On Android 16, apps targeting API 36 can no longer lock orientation or
  resizability on screens of 600 dp and wider. Build adaptive layouts such as
  list and detail, or columns, rather than forcing portrait.
- On the web, test landscape and tablet widths. They expose over-long lines and
  bars that eat the usable height.

## Localised microcopy

- Pick one way of addressing the reader and keep sentences short, with actions
  starting on a verb.
- An error names what failed, does not blame the person and offers a way out:
  "Could not send the order. Check your connection and try again."
- Take dates, currency and numbers from the platform formatter for the person's
  locale, never from a hand-built string. Many languages run 20–30% longer than
  English, so test with long labels.

## Origin

Written for this skill from the public sources linked in this file.
