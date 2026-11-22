# ZaylaTv multi-region electronic programme guide

GitHub Actions downloads schedules from maintained iptv-org/epg adapters, validates future coverage, and publishes XMLTV to GitHub Pages when Pages is configured. The validated guide and coverage report are also available as an Actions artifact.

The published [coverage report](https://yusufstweaks.github.io/ZaylaTv-EPG/status.json) shows how many channels and programmes passed validation.

## Regional selection

Edit `sources.json` before running if you want different regions or channels:

| Selection | Maintained source | Maximum | Selected with the pinned catalog |
| --- | --- | ---: | ---: |
| US | tvguide.com, mapped .us channels in English | 151 | 151 |
| Canada, sports | TSN official public schedule for TSN1–5 and TSN Premium | — | Based on the scheduled events |
| Canada | TV Passport national channels, English/French | 113 | 113 |
| Canada, Ottawa | TV Passport Ottawa over-the-air lineup | 100 | 45 |
| France, primary | Programme-TV.net public listings | 129 | 129 |
| France, fallback | SFR, mapped .fr channels in French | 211 | 211 |
| UK | sky.com, mapped .uk channels in English | 322 | 322 |
| MENA | bein.com Arabic list | 80 | 48 |
| MENA sports | beinsports.com MENA Arabic list | 30 | 14 |
| MENA | bein.com English list | 46 | 46 |
| beIN SPORTS international | Official regional beIN SPORTS grids | 48 | 48 |
| MENA | elcinema.com Arabic list | 102 | 102 |
| South Africa | dstv.com South Africa list only | 60 | 42 |

The pinned catalog supplies 1,271 selected mappings, including overlapping channel IDs across sources. The TSN adapter adds channel IDs from the current schedules. Some mappings may have zero upcoming programmes.

Duplicates within a source are removed using the canonical XMLTV ID before applying its cap. Empty/unmapped XMLTV IDs are excluded. Default selections follow the maintained file's order; they are not a curated recommendation of the best channels. Add an `xmltv_ids` array to a source to select the exact channels from your playlist. Limits can be changed from 1 to 500 per source. For another supported European country, add its maintained list and corresponding country/language filters; this kit does not promise all Europe by default.

The [TSN schedule](https://www.tsn.ca/live/schedule/) supplies explicit start and end times and separate feed IDs for simultaneous TSN Premium events. The publisher preserves TSN’s linear XMLTV IDs and uses `TSNPlus.ca@NN` for streaming feeds. TV Passport can fill TSN1–5 if the official schedule is unavailable.

The Ottawa list is a snapshot of the [TV Passport Ottawa broadcast lineup](https://www.tvpassport.com/lineups/browse/ca/on/12088/ottawa). It captures 45 distinct XMLTV IDs from 46 station entries and includes nearby U.S. affiliates listed by TV Passport. The workflow fetches current schedules from the individual station pages. Entries without an upstream XMLTV ID use a stable `TVPassport.<station number>` ID. Update the checked-in list when the provider changes its lineup.

The beIN SPORTS international list combines the pinned MENA, France, U.S., Australia, and Malaysia mappings. Earlier beIN sources retain priority for matching channel IDs. The pinned New Zealand list has no mapped IDs.

MENA sources are regional lineups, so a foreign broadcaster carried there may still be included. Broadcaster domicile and delivery-region lineup are distinct concepts. Source lists/regions should be checked against your actual playlist.

The UK schedules use Sky. France tries Programme-TV.net first, with SFR covering channels that lack usable listings from Programme-TV.net. If a public Programme-TV.net schedule probe fails, the runner records the error and skips its bulk fetch. The elCinema adapter fetches channel schedules from elCinema’s Arabic site.

## Run on GitHub

1. Set **Settings → Pages → Build and deployment → Source → GitHub Actions**. A private repository needs a GitHub plan that supports private-repository Pages.
2. Open **Actions → Update Zayla TV Multi-Region EPG → Run workflow**. Changes to the workflow, source list, scripts, lineup files or patches on `main` also run it.
3. After deployment, use the Pages URL shown by GitHub with `/guide.xml.gz` in Zayla TV. The site also serves `/guide.xml` and `/status.json`.
4. Use the run's **epg-guide** artifact to download the compressed guide and coverage report while Pages setup is pending.

The workflow uses Node 24, Python 3.11, and a reviewed pinned upstream revision. It patches DStv and configures public Programme-TV.net requests in the pinned checkout. Its `npm ci --ignore-scripts` install is followed explicitly by `npm run api:load` to obtain public metadata required by the grabber. Update the upstream commit deliberately after reviewing/testing changes; a pinned version does not receive site-module fixes automatically.

Each source uses a generated bounded `--channels` list, two days of schedules, at most two concurrent requests, a 15-second per-request override and a ten-minute whole-source process-group budget. The 105-minute job is an outer limit. Stopping a source also stops the npm-launched child process group. Actual schedules and runtime depend on source availability and per-programme requests; ten minutes is not a guarantee a source will finish.

It runs daily at 11:17 UTC and manually. Scheduled Actions can be delayed or disabled after 60 days without public-repository activity. Monitor Actions; scheduled execution is not a guaranteed service.

## Publication rules

- A nonzero grab exit status is recorded; other sources still run.
- XML must have a `<tv>` root, valid known channel IDs, nonempty titles and timezone-aware start/stop timestamps with stop after start.
- No missing time or title is invented; original timestamp values are preserved.
- Channels need at least one programme whose stop is in the future. Fully expired schedules are not presented as current.
- The first usable source owns a channel's complete schedule; later duplicates cannot create overlapping alternate schedules for that channel.
- Every `required_regions` entry in `sources.json` must have some accepted future coverage before deployment. Failed/missing sources within a region may be covered by another configured source in that region. This minimum is not proof of complete regional coverage.
- If a required region is absent, deployment stops and the previous Pages publication remains. This kit does not merge older cached per-region results; it keeps the previous complete deployment as a whole. The previous file may eventually expire and should be monitored.
- If you intentionally disable a region, remove its entries from both `sources` and `required_regions`.

Only generated `public/` files are deployed. `status.json` reports per-source accepted/rejected rows and actual region coverage. The workflow's diagnostic artifact records selected counts and source exit statuses. This is an upstream-dependent process, not a way around a source's access restrictions; HTTP 451 alone does not establish the precise reason for denial.

## Optional Google Drive copy

For an authenticated copy, add the contents of your rclone configuration as the `RCLONE_CONF` repository Actions secret. The default destination is `gdrive:ZaylaTV-EPG`. Set the `GDRIVE_REMOTE_PATH` Actions variable to choose a different remote or folder. Add the configuration only through GitHub Secrets. The secret is checked inside the shell; the temporary config file is removed on exit. A Drive-copy failure is reported without blocking Pages deployment. Copying before deployment is not transactional across Drive and Pages; one destination may update while the other fails. Drive file IDs and sharing must be checked separately before using a link in the app.

## Tests locally

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/configure_programme_tv.py /path/to/iptv-org-epg
cp lineups/ottawa-ota.channels.xml /path/to/iptv-org-epg/sites/
python3 scripts/select_channels.py /path/to/iptv-org-epg sources.json selected
```

For a complete local grab, install the pinned upstream's dependencies and API metadata, then run:

```sh
python3 scripts/run_grabs.py /path/to/iptv-org-epg selected/manifest.json grabbed
python3 scripts/merge_xmltv.py grabbed selected/manifest.json sources.json public
```

The process-group stop uses POSIX signals, matching the Ubuntu Actions runner; adapt it before running on native Windows.

Official references:
- https://github.com/iptv-org/epg
- https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets
- https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows
