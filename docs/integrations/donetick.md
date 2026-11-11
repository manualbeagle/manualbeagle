# Donetick

[Donetick](https://donetick.com) is a self-hosted chore list whose chores repeat. Manual Beagle can send the
maintenance tasks you approve there instead of to Homebox: each one becomes a chore, named for the appliance, that
comes back on the interval the manual gives ("every 2 weeks", "every 6 months") and reminds you when it's due.

Homebox's maintenance entries happen once: when you mark one done, you add the next one yourself. A Donetick chore
repeats by itself, which suits "clean the filter every 2 weeks" better. Pick one with `[maintenance] target`; the
default is Homebox, so nothing changes until you choose Donetick.

It's optional, and off until you set it up. Reading the manuals for maintenance works the same either way.

## What it does

The web UI's **Maintenance** page (see [the web UI guide](../web.md#maintenance)) lists the tasks read from your
manuals. With `target = "donetick"`, each suggestion shows an **Appliance** box (the matching Homebox item's name,
else the brand and model) and **✓ Add to Donetick**. Clicking it:

1. **Reads the token's user** (`GET /api/v1/users/profile`): the chore is assigned to that person.
2. **Finds the label** you set in `[donetick] label`, by name (`GET /api/v1/labels`), if you set one. It must
   exist in Donetick already; Manual Beagle doesn't make labels.
3. **Makes the chore** (`POST /api/v1/chores/`):

| Field | Value |
|---|---|
| `name` | "\<appliance\>: \<task\>", such as "Window AC: Clean the air filter", at most 255 characters. |
| `frequencyType`, `frequency`, `frequencyMetadata.unit` | `interval`, every N `days`, `weeks`, `months` or `years`: the suggestion's interval. |
| `nextDueDate` | The first date you chose (default: one interval from today) at `due_time`, in `timezone`. |
| `isRolling` | `rolling`: false counts the next date from the due date, true from when it's done. |
| `assignedTo`, `assignees` | The token's user. |
| `notification` | `notify`: Donetick reminds that person when the chore falls due. |
| `labelsV2` | The label, when `label` is set. |
| `description` | "Repeat every 2 weeks. From \<manual\> (\<brand\> \<model\>), page N: "\<quote\>"", a link to that page in the web UI when it runs with `--public-url`, and a note that Manual Beagle suggested it and a person approved it. |

The suggestion is then marked `✓ in Donetick` with the chore's id and is never suggested again. If Donetick
refuses, the suggestion stays waiting with the reason. Nothing is sent to Donetick without that click, and Manual
Beagle never changes or deletes a chore: rename, reschedule or archive it in Donetick.

No Homebox item is needed: a suggestion for a model no item has can still be added, named by its brand and model
or whatever you type in **Appliance**.

## Set up

1. **Make an API token.** In Donetick, open **Settings › API Tokens** and make one (a name such as "Manual
   Beagle"). Donetick's tokens have no scopes: one can do anything its user can, so keep it like a password. The
   chores are assigned to that user. Manual Beagle only reads the user's profile and labels and makes chores.
2. **Give it to Manual Beagle** through the environment, a file, or a command, never in the config file itself:

   ```
   export DONETICK_TOKEN=...            # or token_file / token_command in [donetick]
   ```

3. **Point the config at Donetick** (in `config.toml`; the address, the label and the target can also be changed
   on the web UI's Settings page):

   ```toml
   [maintenance]
   target = "donetick"              # default "homebox"

   [donetick]
   url = "http://donetick.local:2021"
   token_env = "DONETICK_TOKEN"     # or token_file = "/path/to/file"
   label = ""                       # such as "Home": a label that exists in Donetick; empty sets none
   due_time = "09:00"               # the time of day chores fall due
   timezone = ""                    # such as "Europe/London"; empty: this computer's own
   rolling = false                  # true: the next date counts from when it's done
   notify = true                    # Donetick reminds you when one falls due
   ```

`MANUALBEAGLE_DONETICK_URL` overrides the address. The token is sent only to the address set in the config file or
the environment: an address changed on the Settings page alone gets no token, so whoever can change settings can't
send it to their own server. The Settings page's Maintenance group says whether a token was found.

## What Donetick sees

Manual Beagle reads the token's user profile and the labels, and writes one chore per suggestion you approve: its
name, interval, first date, the label, and a description with a short quote from the manual and the manual's page.
Nothing else in the library is sent. See [privacy.md](../privacy.md).

Checked against Donetick v0.1.86's API (its `ChoreReq`).
