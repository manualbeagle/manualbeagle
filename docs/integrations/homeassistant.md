# Home Assistant

[Home Assistant](https://www.home-assistant.io/) already knows the smart devices in your home: each one's maker,
model, name and the area it's in. Manual Beagle can read that list to find their manuals and to offer each device
as a new [Homebox](homebox.md) item, in its area as the location, so you don't have to type them in.

It only reads from Home Assistant. It never changes a device, a setting or an entity there.

## What it reads

Manual Beagle opens Home Assistant's WebSocket API (`/api/websocket`), signs in with a long-lived access token,
and sends four list commands, then closes the connection:

| Command | What it gives |
|---|---|
| `config/device_registry/list` | Every device: manufacturer, model, model ID, name (and the name you gave it), area, the config entries it belongs to, whether it's disabled, and whether it's a "service" rather than a device |
| `config/area_registry/list` | Each area's name |
| `config_entries/get` | Each config entry's integration (domain), such as `reolink` or `tplink` |
| `config_entries/get` with `type_filter: ["helper"]` | The helper entries (templates, groups), whose devices are skipped |

None of these needs an admin, so the token of a read-only user is enough. Manual Beagle reads no entity states,
history, camera images or configuration.

When Home Assistant has a **model ID** for a device (Philips Hue lamps, for example, have the model "Hue color
lamp" and the model ID "LCA001"), Manual Beagle searches for the model ID, which is the part number a manual
names. A model like `PD-6WCL (PD-6WCL-XX)` is searched as `PD-6WCL`, and `HL-L2395DW series` as `HL-L2395DW`.

## What it offers, and what it skips

Each device gets one of three results:

- **`+ new`**: it has a maker and a model, and Homebox has no item with the same maker and model. It's offered as
  a new item.
- **`✓ have`**: Homebox already has an item with this maker and model (compared without case, spaces or dashes,
  and with the maker's corporate ending dropped, so "Lutron Electronics Co., Inc" matches "Lutron").
- **`– skip`**, with the reason:
  - disabled in Home Assistant;
  - a service, not a device: Home Assistant marks the sun, weather services, add-ons and the like this way;
  - made by a helper (a template, a group);
  - part of another device: same maker, and a model that names its parent's model (each socket of a smart power
    strip, "Socket for HS300", is part of the HS300);
  - a group of devices, such as a Google Cast speaker group (a model with the word "group");
  - from an integration in `skip_integrations` (by default the sun, backups, the Supervisor, Home Assistant
    itself and its cloud, weather and moon services, and other services that aren't physical things);
  - no maker or no model in Home Assistant (a model of "Unknown", "N/A" or the like counts as none);
  - the same maker and model as a device listed before it. Homebox gets one item per maker and model; that item's
    description names the other devices of the same model.

The maker that goes into Homebox is cleaned the same way: the corporate ending goes ("Signify Netherlands B.V."
becomes "Signify Netherlands"), and a name an adapter knows wins ("LG Electronics" becomes "LG", "Lutron
Electronics" becomes "Lutron").

## Set up

1. **Make a token.** A token can do whatever its Home Assistant user can, so give Manual Beagle a user that can't
   change anything:
   - The simplest is a **non-admin user** made for Manual Beagle (Settings → People → Add person, with "Can only
     log in from the local network" if you like, and "Administrator" off). Sign in as that user once, open its
     profile, **Security**, and create a **long-lived access token**.
   - A non-admin user can still switch your lights. Home Assistant also has a read-only group, `system-read-only`,
     that the UI doesn't offer; a user in it reads everything and controls nothing. Putting a user in it needs the
     admin WebSocket API (`config/auth/update` with `group_ids: ["system-read-only"]`).
2. **Give the token to Manual Beagle** through an environment variable (default `HA_TOKEN`), a file, or a command
   that prints it, the same ways as [Homebox's key](homebox.md#set-up). The token never goes in the config file.
3. **Point the config at Home Assistant:**

   ```toml
   [home_assistant]
   url = "http://homeassistant.local:8123"   # Home Assistant's address, as a browser opens it
   token_env = "HA_TOKEN"                    # the environment variable holding the token
   token_file = ""                           # or a file holding it
   token_command = []                        # or a command that prints it
   # skip_integrations = ["sun", "backup", "hassio", ...]   # integrations whose devices are never offered
   ```

   `MANUALBEAGLE_HA_URL` overrides `url`. The web UI's Settings page can change the address too, but the token
   is only ever sent to the address set beside it in the config file or the environment: an address changed on
   the page alone gets no token, so whoever can change settings can't send your token to their own server.
4. **Check it:**

   ```
   manualbeagle ha status
   ```

   prints `✓ Home Assistant <version> at <url>`, or `✗` and the error.

**A wrong token counts toward Home Assistant's login ban.** By default Home Assistant bans an address after a few
failed logins in a row (`login_attempts_threshold`), and a refused token is a failed login. Manual Beagle reports
a refused token at once and never retries it, but running a command again and again with a wrong token will get
the computer it runs on banned. To lift a ban, delete the address's line from `ip_bans.yaml` in Home Assistant's
configuration folder and restart Home Assistant.

## Look at the devices

```
manualbeagle ha devices          # the devices an import would add, or that Homebox has already
manualbeagle ha devices --all    # also the skipped ones, with the reason
```

## Import them into Homebox

Always look at the dry run first:

```
manualbeagle ha import --dry-run
manualbeagle ha import --dry-run --find      # also look up each device's manual (downloads nothing)
manualbeagle ha import --dry-run --report ha.json
```

Without `--dry-run`, it shows the same list and asks before writing anything:

```
manualbeagle ha import
...
Add 12 new item(s) to Homebox at http://homebox.local:7745? [y]es, [n]o, [e]ach:
```

`y` adds them all, `e` asks for each one, and anything else writes nothing. Without a terminal to ask on (a
script, a scheduled job), nothing is written unless you pass `--yes`, which means you've reviewed the dry run.
`--only TEXT` narrows it to devices whose name contains the text.

Each new item gets the device's name, its maker and model, one unit, its area as the location (made in Homebox
when it has none of that name), and a description saying it came from Home Assistant. Then `manualbeagle sync
homebox` finds and attaches the manuals, as for any other item.

Running the import again is safe: a device whose maker and model Homebox now has shows as `✓ have`.

## In the web UI

The **Homebox** tab has **Import from Home Assistant**. **Read devices** shows every device with its result (tick
**Show skipped devices** to see the skipped ones and why). Each new device has its own **Add…** button, and
**Add N to Homebox…** adds the ticked ones; both ask first. The server reads Home Assistant and Homebox again
when you add, and adds only devices that are still new. **Look up their manuals** runs the devices through Batch
(nothing downloaded), and Sync then finds and attaches their manuals once they're in Homebox. The HTTP API is in
[web.md](../web.md#home-assistant-and-paperless-ngx).

## Code

`src/manualbeagle/sources/homeassistant.py`: the client (`HomeAssistant`, over the `websockets` package's sync
client), `devices_from_registry()`, `plan_import()` (pure, so it's tested without Home Assistant) and
`add_to_homebox()`. The tests use a fake WebSocket in `tests/test_homeassistant.py`.
