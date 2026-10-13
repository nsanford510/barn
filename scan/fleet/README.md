# Scan fleet

Runs the VPT scan for one city on several EC2 nodes in parallel, each with its
own IP and its own segment of the parcels. Every node writes to the same
Supabase project. This computer only watches: **Scan > Fleet** in the web UI
shows per-node and overall progress, scanned/min and ETA, and has
Stop fleet / Resume fleet buttons.

## How it fits together

- `split_parcels.py` deals the parcels that still need a lookup into N disjoint
  segment CSVs (`out/<run_id>/oakland_seg<i>.csv`) and a `manifest.json`.
- `fleet_node.py` runs on each node. The node's `oakland.csv` is its segment, so
  the normal scanner runs unchanged with `restrict_to_csv` (a node only
  re-checks its own segment's rows). It reports through `scanner_workers`,
  `scanner_jobs` (job type `fleet_scan`) and `scanner_job_checkpoints`, and backs
  its cache and removal log up to S3.
- `user_data.sh.tmpl` bootstraps a node (placeholders in `{{braces}}`).
- `merge_artifacts.py` folds the nodes' cache and removal logs back into the
  local `measw_cache.jsonl` and `vpt_removed.jsonl` after the run.
- `webgui/fleet_status.py` builds the status the Fleet tab shows.

Each node paces itself (`VPT_LOOKUP_INTERVAL_SEC`, default 10 s). A node that
the county rejects 5 times in a row marks its job `failed` and waits; nothing
relaunches it or changes its IP. Pressing Resume fleet retries.

## Runbook

1. Stop any scan running on this computer.
2. `python fleet/split_parcels.py --city OAKLAND --nodes 4` (needs `.env` for the
   DB; `--no-db` skips the DB rows).
3. Bundle the code: `tar czf code.tgz find_meas_w_addresses.py db.py worker_state.py geo_utils.py fleet/__init__.py fleet/fleet_node.py fleet/requirements-node.txt`.
4. AWS (us-west-2): private S3 bucket with `runs/<run_id>/code.tgz` and the
   segment CSVs and `supabase.env` (`SUPABASE_URL` and `SUPABASE_SECRET_KEY`
   lines, uploaded from this computer without printing them); role + instance
   profile `barn-scan-node` (EC2 trust, `AmazonSSMManagedInstanceCore`,
   read/write on the bucket's `runs/*`); a security group with no inbound rules.
5. Launch one `t3.small` (Ubuntu 24.04, IMDSv2 required, 16 GiB gp3, shutdown
   behaviour *stop*) per segment with `user_data.sh.tmpl` filled in. Start delays
   of 0/2/4/6 minutes keep the nodes out of step.
6. Watch the Fleet tab for the first ~10 minutes: every node should show
   lookups succeeding with 0 rejections. If not, press Stop fleet.
7. When nodes finish they back up and shut themselves down. Download
   `runs/<run_id>/artifacts/node-*/` and run
   `python fleet/merge_artifacts.py <download_dir>`.
8. Tear everything down: instances, security group, instance profile and role,
   S3 objects (including `supabase.env`) and bucket.

Nodes need about 75 s to install Chromium before they start scanning.
