# Hard-won lessons

Each entry here is a failure that cost us time, data or an account, and the rule we adopted so it does not happen again. They come from about 30 research rounds run by AI agents against two Gramps Web trees and Spanish, French and Italian archives, in October 2026. How the pipeline fits together is in [pipeline.md](pipeline.md).

Format: what happened, why, and the rule.

## Rate limits and blocks

### 1. Volume got a FamilySearch account suspended and an IP blocked

**What happened.** On 2026-10-20 at about 02:40 the account's sign-in page said it was suspended, and one box's IP got `403` with `errorCode 15` from FamilySearch's security layer. That night one agent was harvesting the catalog through the API while about 15 research and sweep lanes called the record and image endpoints in parallel from three IPs.

**Why.** Each lane paced itself, but nothing paced the total. Fifteen polite lanes add up to one abusive client.

**Rule.** One shared gate for every lane that touches the site, with one common pace (see [pipeline.md](pipeline.md#rate-limit-gates)). Every request goes through `wait`, every warning through `hit`, which pauses everyone. No catalog harvests. Cache every good answer so a repeat costs nothing.

### 2. The per-IP ceiling is below about 10 requests a minute, sustained

**What happened.** After the first block we moved to a second account on another IP, with a gate that started at one request every 6 seconds and sped up while there were no warnings. At 10:50 the same day that IP was blocked too: 461 requests in 42 minutes, one every 4.8 to 6 seconds.

**Why.** A gate that speeds up until it gets a warning finds the limit by crossing it, and the penalty for crossing is a day-long block, not a warning.

**Rule.** Restart at one request every 15 seconds, with a floor of 8 seconds. Give each lane a daily budget (we use about 80 requests). A block parks the gate for 24 hours, and lanes carry on with other sources. Do not move the traffic to another IP to dodge a block, and never to the owner's home IP.

### 3. A "no result" during a block is not a negative

**What happened.** Lanes kept recording "nothing found" after the block started. Those lines would have closed searches that were never really run.

**Rule.** Any result from a site after a known block time is suspect. It goes back on the list, and the ledger row is marked `blocked`, not `none`.

### 4. The official API is not an option for personal projects

**What happened.** We checked whether a FamilySearch API key would solve the limits. The application form says personal and read-only projects are rejected, and that the API gives no search over historical records.

**Rule.** Plan for the website's limits, not around them.

### 5. Automation browsers get blocked at login

**What happened.** FamilySearch's login (behind Incapsula) answered 403 and then "session expired" to headless Chromium, and to any browser launched with `--enable-automation` or a user-agent override. Cloudflare's managed challenge never cleared in Playwright's bundled Chromium. The same kind of block hits Geneanet, Gallica, Geni, MyHeritage and the Spanish national library.

**Rule.** Use a real, headed Google Chrome with its own profile, started with `--remote-debugging-port`, and attach with `connectOverCDP`. For FamilySearch, log in there, then take the session cookie and call the API with it as a Bearer token. Run these Chromes on remote boxes. Headed windows on the owner's laptop steal keyboard focus.

### 6. The shared browser profile is not yours

**What happened.** A lane read cookies from `contexts()[0]` on a remote Chrome. That context is the persistent profile other tools and sessions use.

**Rule.** Every lane opens `browser.newContext()` and works only there. To keep a login between runs, save `context.storageState()` to the lane's own file.

## Git and disk

### 7. `git grep HEAD` in a partial clone fetched thousands of packs

**What happened.** To save laptop disk, the research repo was cloned with `--filter=blob:none` and a sparse checkout. Lanes then ran `git grep -n -i <term> HEAD`. Grepping a revision reads blobs from the object store, so git fetched every missing blob from the server one at a time, each as its own pack. A day later the repo had 9,167 packs, the endpoint security agents scanned each new file, and the laptop's load reached 42.

**Why.** In a partial clone, any command that reads many blobs that are not on disk (`git grep <rev>`, `git log -S`, `git log -G`, `git log -p`) turns into one network fetch per object.

**Rule.** Grep the working tree (`rg`, or `git grep` with no revision). For files outside the sparse checkout, use the server's code search or a full clone on a server. `git show HEAD:<path>` for one known file is fine. If load spikes, look for `index-pack --promisor` in `ps` and check `packs:` in `git count-objects -v`.

### 8. Git auto-maintenance repacks filled the disk

**What happened.** The laptop dropped to 1.1 GB free. 10.5 GB of it was `tmp_pack_*` files in the research repo. Every lane commit started `git maintenance run --auto`, which repacked the whole 5 GB store into a temp pack. Several ran at once, ran out of space, and the next commit started them again.

**Rule.** In a large repo that many agents commit to, set `maintenance.auto false` and `gc.auto 0` locally. Repack by hand at a quiet time, on a server.

### 9. Parallel lanes in one checkout share the git index

**What happened.** A lane ran `git add <its paths>` and then `git commit`. The commit also carried two renames another lane had staged.

**Why.** The index belongs to the repository, not to the agent. Staging exact paths limits what you add, not what you commit.

**Rule.** In a shared checkout, commit with `git commit -- <paths>`. Never revert a change that rode along: it is another lane's work.

### 10. Parallel lanes overwrite each other's scratch files

**What happened.** Two lanes wrote the same obvious filename in `/tmp`. One lane's output went out with the other's text, and nothing failed.

**Rule.** Every lane uses its own scratch directory (`mktemp -d`, or a path with the lane name).

## Gramps Web

### 11. Page URLs use the `gramps_id`, not the handle

**What happened.** Links sent to the family pointed at `/person/<handle>`. They opened nothing. `curl` returned 200 for them anyway, because the site is a single-page app that answers 200 for any path.

**Rule.** Build links from the `gramps_id` (`/person/I0042`, `/media/O0007`). Get it from the API (`keys=gramps_id`). Check the link in a browser logged into the right tree: ids repeat across trees on the same server.

### 12. An event's `place` takes a handle, and a `gramps_id` fails silently

**What happened.** Events were written with `place: "P0123"`. The API accepted them, and the events had no place.

**Rule.** Look up the place (`GET /api/places/?gramps_id=P0123`) and pass its `handle`. The check after every apply counts events without a place.

### 13. LLM appliers create duplicates

**What happened.** Applies created the same wedding twice (same family, date and place, from two index entries), and people who already existed from earlier rounds or earlier rows of the same list.

**Why.** An applier working row by row has no memory of what the tree already holds, and two index entries for one record look like two facts.

**Rule.** Before creating anyone, search the live tree by name, without accents and with alternative spellings. Before creating a place, search all places the same way. The check after the apply lists repeated vital events (same object, same type, same date). Merge by keeping one event, moving the citation over, noting the other index id in the description, and deleting the duplicate only after it has no backlinks.

### 14. Text files uploaded as media are grey icons nobody can read

**What happened.** 41% of the media objects in our trees (4,564 of 11,067) were transcripts, JSON, TSV, HTML, `.eml` and archives. Gramps Web has no preview for them. The gallery shows a grey document icon, and clicking it opens the same icon full screen. The owner reported the media as "broken".

**Rule.** Only images, PDFs, and public audio or video go in as media, in their original format. Transcripts go in as notes of type *Transcription* on the citation, with the repo path on the first line. Raw captures (JSON, HTML, logs) stay in the repo, and a *Research* note records what was searched and where the file is.

### 15. The media route that works for bulk applies

**What happened.** Gramps Web offers several ways to create media: upload the file body to `/api/media/`, upload a ZIP, or create an object that points at a file already on disk. Early rounds also cited sources by URL only, with no image inside the tree.

**Rule.** For bulk applies, use one route: copy the file into the tree's media folder (with per-tree media prefixes, each tree reads only its own folder), then create the object with `POST /api/objects/` pointing at that path, and link it to the citation in the same pass. A link alone is not enough: the image goes inside the tree.

### 16. A full search reindex takes the site down for 10 to 16 minutes

**What happened.** After an apply, a full reindex (`/api/search/index/` with `full=1`) left the web app on "No connection" for 10 to 16 minutes each time.

**Rule.** Do not run it after an apply. The incremental index updates on every write. If a check needs search, run one search and accept the result.

### 17. A GEDCOM import adds, it does not merge

**Rule.** Back up before importing into a tree that has data. An import into a populated tree duplicates people.

### 18. A merge brings the loser's events along, and a second run of the list does not skip

**What happened.** A correction merged 66 people who had been created twice (the same baptism from two index rows). Each merge kept both copies of the baptism, or a year-only Birth next to a dated Baptism, so the check after the apply listed 14 repeated vital events. Gramps also merged on its own every family that ended up with the same two spouses (22 of them), so the families named in later rows of the list no longer existed. Re-running the list on the applied tree, expecting every row to come back `skip`, stopped instead with `[conflict] no Family with Gramps ID F0191`.

**Rule.** After a person merge, look at the winner and drop the loser's copy of each vital event: move its citation to the kept event, then delete it once it has no backlinks. A family merge whose spouses only coincide after a person merge goes in a second list, applied after the first; the offline dry run cannot see it, because the dry run only marks the loser deleted. Prove the apply landed with the check and the post-apply snapshot, not by replaying the list.

### 19. A known finding is matched by its whole key

**What happened.** The known file listed `I0830` for a `surname-marker` finding whose key is `I0830 Bron[…]te`. The check reported it as new and exited 1.

**Rule.** Copy the key from the `--json` report of the previous run. The key is the whole string, not the id in it.

## Sources and images

### 20. A screenshot of a viewer is not a source

**What happened.** A wedding record was saved as a 4000×3000 screenshot of the FamilySearch viewer with a side panel open. The panel's overlay dimmed the whole page, and the browser bar, panel and thumbnail strip were inside the image. Grey, unreadable, and full of interface.

**Rule.** Ask where the evidence is. If it is a scanned document in a viewer (parish book, civil register, census, newspaper page), download the image itself: the download button, IIIF at full size (Antenati, Gallica), the portal's PDF or JPG. If the evidence is the page's own text (an indexed record, an archive catalogue entry, a database row), save a clean full-page capture and its text. If both exist, keep both.

### 21. Look at every image before you keep it

**What happened.** Saved captures included cookie banners, Cloudflare's "One moment..." page, half-loaded pages and tiny thumbnails where a full-size image existed.

**Rule.** The agent opens every image before saving or uploading it, and checks that it shows what the citation says. If not, it captures again. An image you cannot read is not a source.

### 22. The index and the OCR are a lead, the image decides

**What happened.** Verifiers kept finding machine readings that were wrong: "February" where the scan says "January", a ditto mark (`id.`) read against the wrong line, a number in a sum copied from the line above, an image number taken for an amount.

**Rule.** An index line or OCR text makes a claim *probable* at most. It becomes *verified* when someone has read the image. Every lane's report goes through a verifier that reopens the sources (see [pipeline.md](pipeline.md#corrected-after-verification)).

### 23. Reading scans as images grew agent transcripts to gigabytes

**What happened.** Two sweep agents told to read scanned PDFs page by page with an image tool grew their transcripts to 6.3 GB and 3.7 GB in six hours. They admitted to appending findings without seeing the pages, and then died. They could not be revived, because reloading the transcript would have run the machine out of memory.

**Rule.** `pdftotext -layout` first. Only for pages with no text layer, render one page at a time at low resolution, make a copy no larger than 1600 px on the long side, read it, append the finding to a report file, delete the image, and move on. Never say a page was read unless the image actually came back. Watch transcript sizes during long sweeps.

### 24. High-resolution scan viewers crash remote browsers

**What happened.** Scan viewers ran a remote Chrome out of memory seven times in two days.

**Rule.** Download the file instead of opening it in the viewer. It is lighter and it is the better source anyway.

### 25. A remote Chrome can look healthy while every page hangs

**What happened.** A remote Chrome answered `/json/version` and accepted websocket connections, but `Runtime.evaluate` timed out on every target, and Playwright's `connectOverCDP` timed out after 30 seconds. The renderers had wedged after two days of lanes.

**Rule.** Health-check with a real attach and an evaluate, not with `/json/version`. If it hangs, restart the browser container and its CDP proxy, after checking no lane holds the box.

### 26. Negative searches are invisible

**What happened.** Lanes repeated each other's searches across rounds, because a negative result leaves nothing in the tree.

**Rule.** Use a shared ledger ([`ledger/`](../ledger/)). Check it before every search, append to it after, negatives included.

## Spanish civil registry (DICIREG) and e-notifications

### 27. One request per applicant, person and certificate type, every 7 days, across all offices

**What happened.** A death certificate request to one civil registry office was refused because the same request had gone to a different office the day before. No case file is created on a refusal. Our older notes said 15 days. The online office's message says 7.

**Rule.** When a certificate may need several offices, chain them 7 days apart and put each next date in a calendar.

### 28. Search your own case files before filing

**What happened.** A round's plan said a certificate had "never been requested". It had been, two days earlier.

**Rule.** Before filing, search "my case files" by the person's name and both surnames, with no dates. Searching that way never returns "too many results".

### 29. Beyond second degree, the registry wants proof of kinship

**What happened.** Twelve requests for great-great-grandparents' records were refused in one day for not proving a legitimate interest (Spain's Ley 20/2011, art. 15.3). Interest is only presumed up to the second degree of kinship.

**Rule.** For anyone further back, attach the chain: the requester's ID, family book, and the birth records linking each generation to the person requested. Plan to collect that chain once per branch.

### 30. The registry sometimes answers only through the e-notification inbox

**What happened.** A certificate arrived only in the government e-notification inbox (DEHú) on 2026-10-15. The email alert just said "new electronic communication", with no case number. Nobody opened it until 2026-10-20.

**Rule.** Every mail pass also opens the e-notification inbox of each requester, with their certificate. Genealogy items are downloaded into the repo. Anything else (fines, tax) is not opened: report the sender and expiry date to the owner.

### 31. Small form traps on the online office

**What happened.** A semicolon in the free-text "Observations" field failed the form at step 4 with "wrong format". Periods, commas, colons, hyphens and parentheses pass. Some regional offices add a language pop-up with its own send button. Downloading the receipt needed CDP's `Browser.setDownloadBehavior`.

**Rule.** Keep observations to plain punctuation. Read the confirmation screen and save the receipt every time.

### 32. Signing a PDF is not a human step, and `pdfsig` lies about it

**What happened.** A filing was left "waiting for a signature" when the certificate was already in the machine's keychain. Then poppler's `pdfsig` reported "Signature is Invalid" on a PDF signed with AutoFirma. pyhanko said the same signature was intact, valid, covered the entire file, and trusted.

**Rule.** AutoFirma's bundled command line signs with no picker: `autofirma.jar sign -i in.pdf -o out.pdf -format pades -store mac -filter "subject.contains:<ID number>"` (`-alias` fails). Validate with pyhanko, not `pdfsig`.

## Letters to archives and people

### 33. Never offer to pay or to visit

**What happened.** Early drafts said things like "if there is a fee or I need to come in person, let me know". That makes it easy for the archive to answer "come in" or "pay".

**Rule.** Ask for the record, free and by mail. If they set a condition, log it in the costs register and decide later. The default is to wait: most things get digitized.

### 34. Never hand relatives work an agent can do

**What happened.** An agent sent a relative a long list of parishes to phone. The owner had to tell them to ignore it. A priest asked for a WhatsApp message; email worked fine.

**Rule.** For any archive, parish or registry, find the email or the web form and send it ourselves. Ask relatives only for what nobody else has: memories, papers at home, a call where no written channel exists. Batch those questions into one message, not several small ones.

### 35. Filled request forms arrived garbled

**What happened.** An archive wrote that our filled request sheets "arrive completely misaligned, and what should take one page takes three or four". They had looked fine in our PDF viewer.

**Rule.** Fill the official PDF without changing page size or layout. Flatten the fields into the page content. Render it with two different engines (`pdftoppm` and Chrome, for example), check the page count matches the blank form, and look at every page. If the text does not fit, use more sheets, never smaller text.

### 36. An archive that asks for silence gets silence

**What happened.** A saturated archive asked us not to write again unless strictly necessary, and said they would contact us.

**Rule.** Log it in the waiting register. Any new or corrected sheet waits for their next message and goes in the reply. Searches that need staff time without an exact date move to the in-person list.

### 37. A sent forum message cannot be taken back

**What happened.** A Geneanet message went out as one block of text, because the rich-text editor had not loaded and the form was submitted directly. Deleting it only moved the thread to our trash. The recipient kept it, and the corrected copy arrived as a second message.

**Rule.** Wait for the editor (TinyMCE), set the content with one `<p>` per paragraph, screenshot the box, then send. Get the final text approved before the first send.

### 38. Read how a person refers to themselves before writing to them

**What happened.** A gendered greeting went to someone of the other gender. Separately, a reply in the formal register went to someone who had written informally, which read as cold.

**Rule.** Before drafting, check how the person signs or refers to themselves, and which register they use. If unknown, use neutral wording and the formal register.

## The agent harness

### 39. The workflow harness passes the last chat message to every agent as the user's voice

**What happened.** An apply was launched right after the owner answered an unrelated question with "Yes, send the message". The read-only stage ran. Every writer stopped with 0 rows applied, saying the user's words did not approve a large write to the live tree.

**Why.** The harness tells each agent that the latest user message overrides the task text. Writers read an unrelated or questioning message as "no approval". Research lanes, which only write files, were not affected.

**Rule.** Get a standing approval for applies of judged rounds written into the project rules, and put that line, verbatim, in every writer's brief, with a note that the relayed message belongs to another conversation. Check the run's journal a few minutes after launch.

### 40. A workflow resume caches only the unchanged prefix of calls

**What happened.** A resumed run had new notes on nine dead lanes. A tenth lane that had already finished kept its prompt byte-identical and ran again anyway.

**Why.** Resume replays calls from cache only up to the first changed prompt, in call order. Everything after that runs live, finished or not.

**Rule.** Pass finished lanes' results in the script's arguments (`done`) so the script skips them by state. Check live agents a few minutes after a resume, and stop at once if a finished lane is writing.

### 41. A second resume must carry every earlier note

**What happened.** A second resume passed notes only for the newly dead stages. The cached prompts of earlier stages contained the first resume's notes, so they no longer matched, and finished builders started again.

**Rule.** Before each resume, collect every earlier note for that run and pass them verbatim, plus the new one. Within a minute, confirm the journal's new "started" line names the dead stage, not a finished one.

### 42. The harness restarts limit-killed agents from scratch

**What happened.** A usage limit killed seven workflow agents, two of them tree writers. When the limit reset, the harness started a fresh agent for each, with the same prompt and no memory of the work already done. For a writer, that risks writing everything twice.

**Rule.** After a limit, look for a second "started" line with the same label. If it is there, stop the run at once and resume it with a note that hands each agent its dead predecessor's transcript and where it stopped.

### 43. A lane's "done" is not final if you have sent it a follow-up

**What happened.** A lane reported done. Main sent it a follow-up, then cleaned up its worktrees. The lane was mid-edit and lost its work twice.

**Rule.** Remove a lane's worktree only after its final report, and only if no follow-up has gone to it since. Or let the lane remove its own.

## Applying change lists

### 44. A namesake flag without a reason let duplicates in

**What happened.** One apply of about 8,800 ops carried 125 `allow_namesake` flags, none with a reason. Several of those people were already in the tree under the same name, and one ruling row went into the list twice under two keys.

**Why.** The flag only said "let it through". Nothing asked which existing person had been compared, and two ops that differ only in their key look like two changes.

**Rule.** `allow_namesake` needs `namesake_reason` and `namesake_of`, the IDs of the people compared; a namesake the op does not name is still a conflict. Two ops that are the same change are refused. Before the first write, every new person is compared with the tree and the rest of the list by name and birth year, parents or spouse, and all likely duplicates are reported at once.

### 45. "Probable" marks on parent links were lost

**What happened.** 218 child links went in without the "probable" mark the rulings gave them.

**Why.** The mark was a separate note op, and `link_child` had nowhere to say it. When the note op was left out, nothing noticed.

**Rule.** `link_child` takes `probable: true` with `probable_reason` and writes the `[probable]` note on the child_ref itself. Rerunning it on an existing link adds a missing mark.

### 46. Two copies of one apply ran on the same tree at once

**What happened.** A second copy of an apply was started on a tree while the first was still writing. They interleaved their writes, and the second overwrote the backup file the first had taken after its block.

**Why.** Nothing in the tree said an apply was running, and the backup and log names came from the date and the block label, which both copies shared.

**Rule.** `--apply` holds a lock note inside the tree for its whole run and refuses to start while another run holds it. Every run has its own id, and its log is created with `O_EXCL` under a name that carries the id, so no run can write into another run's file. Backup names carry a run id too, and are created the same way.

### 47. One slow answer killed a long apply

**What happened.** About 1,000 ops into a long apply on a test server, one `POST` got no answer for two minutes: the server's cache connection had stalled. The client's read timeout fired, the exception was not one `gwt-apply` retried, and the run stopped there. The create may or may not have reached the database.

**Why.** Only an HTTP answer ("database is locked", a 5xx) counted as "busy, try again". A timeout or a dropped connection has no answer at all, so it crashed the run, and a naive retry of a create whose answer was lost could make the object twice.

**Rule.** A timeout or a lost connection starts the op over from fresh reads, like a busy server. A create that got no answer is looked up by its handle before the op tries it again, so it is linked if the server made it and made once if not.

### 48. Duplicates were merged by hand

**What happened.** During an apply, a person turned out to be an empty duplicate of someone already in the tree. The merge was done by hand: the duplicate's family, citation and note were moved to the other person with separate `PUT`s, and the duplicate was deleted. Nothing showed the whole change before it ran, and nothing on the surviving person said a merge had happened.

**Why.** `gwt-apply` had no merge op, so a merge meant leaving it and writing to the API directly, where every reference to move is one more write that can be forgotten.

**Rule.** `merge_person` and `merge_family` call Gramps' own merge on the server, which moves every reference itself. The dry run lists what moves, which families are repointed or merged, and what is deleted. The winner gets a note naming the loser, the ruling and the reason, written before the merge, so a rerun knows the merge is done.

### 49. A Spanish civil registry refused a third-party request for lack of five things

**What happened.** A Registro Civil refused two birth-act requests made for ancestors by a descendant. The reasons given: no proven legitimate interest, no family authorization or ID copy, no concrete purpose, no anonymisation, and no guarantee of use. The refusal opened a one-month appeal to the Dirección General de Seguridad Jurídica y Fe Pública.

**Why.** The first request said only "genealogy". Ley 20/2011 lets a third party see another person's entries only with a legitimate interest (art. 15.3), and the only interest it presumes is that of the spouse or partner and of ascendants and descendants up to the second degree (art. 84). A great-grandchild is past that, so every further link has to be documented. For a person who is still living, the sede offers only "legal representative" or "authorized by the registered person", so an adult child has no route without that person's authorization. For a deceased person it offers "children, spouses and partners of" or "others".

**Rule.** State the interest, the kinship with a document for each link past the second degree, the concrete purpose, that third parties will be anonymised and that the use is private, in the first request. Set "registered person deceased" before "in the capacity of". Before filing again, look in "my cases"; then wait 15 days before asking the same office again, and 7 days before asking another office with the same certificate. Log a refusal in `gwt-ledger` as `denied` with `--next-date` a few days before the last day of the appeal, and run `gwt-ledger due` at the start of each round. The appeal is signed with a digital certificate through AutoFirma.
