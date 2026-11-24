# ZaylaTv multi-region electronic programme guide

GitHub Actions downloads schedules from maintained iptv-org/epg adapters and official JSON APIs, validates future coverage, and publishes both **per-region XMLTV files** (`regions/<region>.xml.gz`, e.g. `regions/us.xml.gz`, `regions/ca.xml.gz`, `regions/uk.xml.gz`, `regions/fr.xml.gz`, `regions/mena.xml.gz`, `regions/za.xml.gz`, `regions/fast.xml.gz`) and a combined `guide.xml.gz` to GitHub Pages. All validated guides and the coverage report are also saved in the `epg-guide` Actions artifact.

The published [index page](https://yusufstweaks.github.io/ZaylaTv-EPG/) and [coverage report](https://yusufstweaks.github.io/ZaylaTv-EPG/status.json) list every per-region file URL along with channel counts, programme counts, and compressed sizes.

## Regional selection

The publisher adds verified, public station ID aliases from `lineups/channel-id-aliases.json` as extra XMLTV display names. Each alias applies to its configured region and an existing guide channel with usable listings. The original channel ID, display name, and programme references stay intact. The coverage report records the number of aliases added. Keep aliases specific to the station or edition, and use public identifiers only.

Edit `sources.json` before running if you want different regions or channels:

| Selection | Maintained source | Maximum | Selected / behavior |
| --- | --- | ---: | --- |
| US, sports | ESPN official public schedule for ESPN linear networks and ESPN+ events | — | Based on scheduled events |
| US, pro teams | ESPN public scoreboard API for NBA, NHL, NFL & MLB team channels (`pro_teams`) | — | Up to 124 team channels (7d NBA/NHL/MLB, 14d NFL) |
| US | TV Guide official multi-lineup JSON API (National, DirecTV, Dish, Spectrum + 9 major metro OTA markets) | — | ~1,900 deduplicated US channels (preserves canonical `tvguide.com` IDs & ESPN priority) |
| US, fallback | TV Passport US (`tvpassport.com`) | 400 | Additional mapped `.us` channels |
| Canada, sports | TSN official public schedule for TSN1–5 and TSN Premium | — | Based on scheduled events |
| Canada | TV Passport national channels, English/French | 130 | 113 |
| Canada, Ottawa | TV Passport Ottawa over-the-air lineup | 100 | 45 |
| Canada, regional & specialty | Checked-in TV Passport Canada extra lineup (`lineups/canada-extra.channels.xml`) | 120 | 86 regional CBC/CTV/Global/Citytv/Noovo/TVA/specialty stations |
| Canada, French | TV Hebdo (`tvhebdo.com`) | 200 | 152 mapped Quebec / Canadian French channels |
| Canada, fallback | OnTVTonight Canada (`ontvtonight.com`) | 100 | 82 mapped `.ca` fallback channels |
| France, primary | Programme-TV.net public listings | 180 | 129 |
| France, fallback | SFR (`tv.sfr.fr`, French + European/MENA channels on SFR) | 450 | 438 |
| UK & Ireland | Sky (`sky.com`), Virgin Media (`virgintvgo.virginmedia.com`) & MyTelly (`mytelly.co.uk`) | 500 / 200 / 350 | UK & Ireland Sky + Virgin + Freeview/regional coverage |
| UK, football clubs | ESPN public scoreboard API for Premier League, EFL Championship & Scottish Premiership clubs (`pro_teams`) | — | 56 UK football club channels (14d + UEFA/FA/EFL Cup matches) |
| Germany / Austria (optional) | Sky DE/AT (`sky.com`) + Swisscom Blue (`tv.blue.ch`) | 300 / 220 | German & DACH channels |
| Switzerland (optional) | Teleboy (`teleboy.ch`) | 300 | Swiss German/French/Italian channels |
| Belgium (optional) | Telenet (`telenet.tv`) | 120 | Belgian channels |
| Italy (optional) | Sky Italy (`sky.com`) | 220 | Italian channels (incl. Sky Sport IT) |
| Spain (optional) | El País (`programacion-tv.elpais.com`) + Movistar Plus+ (`movistarplus.es`) | 120 / 150 | Spanish FTA & Movistar channels |
| Netherlands (optional) | Ziggo GO (`ziggogo.tv`) | 160 | 148 Dutch channels (incl. NPO & ESPN NL) |
| Poland (optional) | Onet Program TV (`programtv.onet.pl`) | 80 | 80 Polish channels |
| Portugal (optional) | MEO (`meo.pt`) | 210 | 210 Portuguese channels |
| Romania (optional) | ProgrameTV (`programetv.ro`) | 160 | 160 Romanian channels |
| Nordics (optional) | TV24 (`tv24.se`) | 160 | Swedish, Danish & Norwegian channels |
| Czechia / Slovakia (optional) | MujTVProgram (`mujtvprogram.cz`) | 200 | Czech & Slovak channels |
| Türkiye (optional) | TV+ (`tvplus.com.tr`) | 160 | 144 Turkish channels |
| Greece (optional) | Cosmote TV (`lineups/greece-cosmote.channels.xml`) + Digea (`lineups/greece-digea.channels.xml`) | 120 / 80 | 181 Greek pay-TV & FTA channels |
| Albania / Kosovo (optional) | IPKO (`ipko.tv`) | 180 | 158 Albanian / Kosovo / regional channels |
| Balkans (optional) | NEO (`neo.io`) + T-2 (`tv2go.t-2.net`) | 230 / 240 | Serbian, Slovenian, Bosnian & Croatian channels |
| Malta (optional) | Melita (`melita.com`) | 120 | 110 Maltese & regional European channels |
| MENA | bein.com (AR/EN), beIN SPORTS API (`ar-mena`), elCinema, Shahid/MBC, Rotana & OSN (`osn.com`) | 80–120 | Core Arabic/English MENA & sports schedules |
| beIN SPORTS international | beIN SPORTS official Opta API (FR, US, AU, MY, HK, PH, NZ) | — | ~28 active international channels |
| South Africa | DStv South Africa official JSON API (`country=zaf`) | — | All 149 ZA channels (incl. SABC 1/2/3/News & SuperSport) |
| Africa (optional) | StarTimes (`startimestv.com`) | 80 | 58 |
| Australia & New Zealand (optional) | Freeview AU + Binge (`au-fast`) & Sky Go / Sky Sport Now NZ (`nz-fast`) via `grab_fast.py` | — | Australian & New Zealand schedules |
| FAST streaming (optional) | Pluto TV, Plex, Roku, Samsung TV Plus & PBS (`grab_fast.py` via `i.mjh.nz` canonical IDs) | — | ~1,000+ canonical FAST/PBS channels |
| India (optional) | Airtel Xstream (`airtelxstream.in`), Tata Play (`tataplay.com`) & DishTV (`dishtv.in`) | 400 / 400 / 350 | Indian national, regional & sports channels |
| Brazil / Mexico / LatAm (optional) | Mi.tv (`br`, `mx`, `ar`/`co`/`cl`/`pe`), GatoTV (`gatotv.com`) & SIBA (`siba.com.co`) | 100–300 | Split per market so Brazil does not crowd out MX & South America |

Duplicates within a source are removed using the canonical XMLTV ID before applying its cap. Empty/unmapped XMLTV IDs are excluded for iptv-org XML catalogs. Custom official JSON/XML adapters (`espn`, `pro_teams`, `tsn`, `tvguide`, `beinsports`, `dazn`, `dstv_za`, `fast`) preserve canonical `iptv-org` XMLTV IDs where known and assign deterministic country-suffixed IDs for additional official channels. Optional regions (`DE`, `CH`, `BE`, `IT`, `ES`, `NL`, `PL`, `PT`, `RO`, `SE`, `CZ`, `TR`, `GR`, `AL`, `BALKANS`, `MT`, `AU`, `NZ`, `AF`, `SPORTS`, `FAST`, `IN`, `BR`, `MX`, `LATAM`) are excluded from `required_regions` so an optional regional hiccup never blocks publication of core regions (`US`, `CA`, `FR`, `UK`, `MENA`, `ZA`). `scripts/run_grabs.py` runs up to 3 independent target domains concurrently while strictly serializing sources that share a site domain.

The [ESPN schedule](https://www.espn.com/watch/schedule/_/country/us/redirected/true) adapter queries ESPN’s public Watch schedule feed for linear channels (`ESPN.us@SD`, `ESPN2.us@SD`, `ESPNU.us@SD`, `ESPNews.us@SD`, `SECNetwork.us@SD`, `ACCNetwork.us@SD`, and `ESPNDeportes.us@SD`) ahead of TV Guide so ESPN takes priority for matching channel IDs while TV Guide automatically fills any channel where ESPN has no usable listings. Published end times are preserved; missing end times are inferred from the next start only on linear channels (marked as an estimate in `<desc>`, omitting a final programme with no determinable end) and are never inferred across simultaneous ESPN+ events. ESPN+ events are collected and deduplicated by ESPN airing/event ID, broadcast feed (`feedName`), and language while preserving genuinely distinct broadcasts and languages. ESPN+ events are only assigned to playable playlist channels via explicit rules in `lineups/espn-plus-map.json` (never auto-guessed onto synthetic `ESPN+ 01` numbers); unmapped events remain on deterministic, refresh-stable `ESPNPlus.unmapped.*.us` IDs labelled `[Unmapped ESPN+] ...` so they cannot display against an unrelated playable channel.

The [TSN schedule](https://www.tsn.ca/live/schedule/) supplies explicit start and end times and separate feed IDs for simultaneous TSN Premium events. The publisher preserves TSN’s linear XMLTV IDs and uses `TSNPlus.ca@NN` for streaming feeds. TV Passport can fill TSN1–5 if the official schedule is unavailable.

The Ottawa list is a snapshot of the [TV Passport Ottawa broadcast lineup](https://www.tvpassport.com/lineups/browse/ca/on/12088/ottawa). It captures 45 distinct XMLTV IDs from 46 station entries and includes nearby U.S. affiliates listed by TV Passport. The workflow fetches current schedules from the individual station pages. Entries without an upstream XMLTV ID use a stable `TVPassport.<station number>` ID. Update the checked-in list when the provider changes its lineup.

The beIN SPORTS Opta API adapter (`grab_beinsports.py`) runs ahead of `bein.com` for MENA (`beIN SPORTS 1–9`, `MAX 1–6`, `EN 1–2`, `FR 1–2`, `XTRA 1–9`, `NEWS`, `4K`, and Global FTA while `bein.com` supplies Alkass, beIN Movies/Series/Drama, and fallback), publishes US (`us-beinsports`), Canada (`ca-beinsports`), and Australia (`au-beinsports`) directly into their regional guides, and combines international feeds (France, U.S., Australia, Singapore/Malaysia, Hong Kong, Philippines, New Zealand) in `regions/sports.xml.gz`. Earlier sources retain priority for matching channel IDs.

The DAZN linear adapter (`grab_dazn.py`) queries DAZN’s public Rail-Router schedule feed (`rail-router.discovery.indazn.com`) for curated DAZN linear channels across Germany (`DAZN 1/2`, `DAZN RISE`, `DAZN FAST+`, `DAZN MLB`, `DAZN NFL Network`), Spain (`DAZN 1/2`, `DAZN F1`, `DAZN LALIGA`), Italy (`Zona DAZN`), France (`Ligue 1+`), Portugal (`DAZN 1–5`), the UK (`DAZN Red Bull TV`), and the US (`DAZN 1 USA`). Checked-in carrier lineups `lineups/spain-dazn.channels.xml` (Movistar+ Spain: `DAZN 1–4`, `DAZN MotoGP`, `DAZN Baloncesto 1–3`) and `lineups/belgium-dazn.channels.xml` (Telenet Belgium: `DAZN Pro League 1–3` and `DAZN 1–3` FR/NL) fill linear channels that are unmapped or assigned wrong country codes upstream.

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
