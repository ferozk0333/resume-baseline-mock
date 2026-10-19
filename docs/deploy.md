# Prototype deployment: one EC2 instance for 30 days

One `t4g.medium` (ARM64, 2 vCPU, 4 GB) in `us-west-2`, Ubuntu 24.04 LTS, 30 GB gp3, Docker Compose. Plain HTTP on
port 80, no HTTPS, no backups. The security group lets in only your IP (ports 80 and 22). The server starts from a
clean reseed: synthetic profiles and demo jobs only.

**Approximate cost** (us-west-2 on-demand list prices; check the AWS pricing pages before you start):

| item | price | 30 days |
|---|---|---|
| t4g.medium | ~$0.0336 per hour | ~$24.50 |
| 30 GB gp3 | ~$0.08 per GB-month | ~$2.40 |
| public IPv4 address (also an Elastic IP while attached) | ~$0.005 per hour | ~$3.65 |
| data transfer out | first 100 GB per month free | ~$0 |
| **total** | | **about $30** (plus model usage: Jev is capped by `JEV_DAILY_MAX_REQUESTS`) |

What runs (`docker-compose.prod.yml`):

| service | image | role |
|---|---|---|
| `proxy` | `caddy:2-alpine` | `:80` → web at `/`, API at `/api` (prefix stripped), `/healthz`; server-sent events unbuffered; request body capped at `PROXY_MAX_BODY_MB` |
| `web` | `frontend/Dockerfile` | Next.js standalone (production) |
| `api` | `Dockerfile` | FastAPI, one process: the background jobs (inbox poll, reminders, nudges) run here only |

Data (database, uploads, the judgment archive) lives in `~/rightstaff/data` on the instance and survives rebuilds.
It is not backed up: terminating the instance deletes it.

Commands below run on **your machine** unless the heading says **server**. Replace `<…>` placeholders.

---

## 1. Launch the instance (your machine, AWS CLI)

Needs the AWS CLI with credentials for your account (`aws configure`).

```bash
export AWS_REGION=us-west-2
MYIP=$(curl -s https://checkip.amazonaws.com)/32

# Ubuntu 24.04 LTS, ARM64, current AMI (published by Canonical)
AMI=$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
      --query Parameter.Value --output text)

# SSH key pair (keep the .pem private)
aws ec2 create-key-pair --key-name rightstaff --key-type ed25519 --query KeyMaterial --output text > ~/.ssh/rightstaff.pem
chmod 600 ~/.ssh/rightstaff.pem

# Security group: ports 22 and 80 from your IP only
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 create-security-group --group-name rightstaff --description "RightStaff prototype" --vpc-id $VPC \
     --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $SG --protocol tcp --port 22 --cidr $MYIP
aws ec2 authorize-security-group-ingress --group-id $SG --protocol tcp --port 80 --cidr $MYIP

# t4g.medium with a 30 GB gp3 root volume (deleted with the instance)
ID=$(aws ec2 run-instances --image-id $AMI --instance-type t4g.medium --key-name rightstaff \
     --security-group-ids $SG \
     --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":30,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
     --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=rightstaff}]' \
     --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --instance-ids $ID
IP=$(aws ec2 describe-instances --instance-ids $ID --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "instance $ID at $IP (security group $SG)"
```

The public IP changes if you stop and start the instance. To keep one address, allocate an Elastic IP
(`aws ec2 allocate-address`, then `aws ec2 associate-address --instance-id $ID --allocation-id <eipalloc-…>`) and
release it at teardown.

**Let someone else in for a demo** (their IP only, port 80), then remove it afterwards:

```bash
aws ec2 authorize-security-group-ingress --group-id $SG --protocol tcp --port 80 --cidr <their.ip.address>/32
aws ec2 revoke-security-group-ingress    --group-id $SG --protocol tcp --port 80 --cidr <their.ip.address>/32
```

If your own IP changes, add the new one the same way (and the port 22 rule too) and revoke the old one.

## 2. Install Docker, git and swap (server)

```bash
ssh -i ~/.ssh/rightstaff.pem ubuntu@$IP

sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 docker-buildx git sqlite3
sudo usermod -aG docker ubuntu
exit                                    # log out and back in so the docker group applies
ssh -i ~/.ssh/rightstaff.pem ubuntu@$IP
docker version && docker compose version

# 1 GB swap as a safety margin for builds
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

## 3. Clone the repository and create .env (server)

```bash
git clone https://github.com/<owner>/<repo>.git ~/rightstaff
cd ~/rightstaff
git checkout feature/deploy             # or main once it is merged
mkdir -p data                           # the data volume (owned by ubuntu, uid 1000 = the containers' user)

cp .env.prod.example .env
chmod 600 .env
nano .env                               # fill in at least the "Required" block; .env is never committed
```

Use **separate keys for the server** where you can (a Gemini and a Groq key for this project, a Jev key you can
revoke), and an **app password** for a demo mailbox, not your main password. Suggested values:
`LIVE=1`, `LLM_PROVIDER=gemini`, `JUDGE=jev`, `JEV_DAILY_MAX_REQUESTS=300`, `CLAUDE_DAILY_MAX_REQUESTS=0`,
`EMAIL_PROVIDER=smtp` (or `outbox` to send nothing), `DEMO_EMAIL_TO=<your inbox>`, `MIN_POOL=5`, `MIN_GROUP=2`.
The API refuses to start without `DEMO_EMAIL_TO`, and while it is set no email goes to anyone else.

## 4. Copy the judgment archive (your machine)

The archive holds the saved judgments of the synthetic demo profiles only (never a real person), so the reseed scores
them without model calls.

```bash
make archive                            # data/judgment-archive.db, checked: synthetic profiles only
scp -i ~/.ssh/rightstaff.pem data/judgment-archive.db ubuntu@$IP:~/rightstaff/data/
```

## 5. Build, reseed, start (server)

```bash
cd ~/rightstaff
docker compose -f docker-compose.prod.yml build                    # ~5–10 min the first time
docker compose -f docker-compose.prod.yml run --rm --no-deps api python scripts/reseed.py --force
#   prints each synthetic profile's score and "Unrecorded questions: 0" (no model calls)
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps                       # all three "healthy" after ~30 s
```

## 6. Verify

```bash
curl http://$IP/healthz                 # ok   (your machine)
```

1. Open `http://<IP>/` in your browser: the candidate Home loads; Recruiter shows the three demo jobs with scored
   synthetic applicants.
2. **Upload a synthetic resume:** use a rendered test profile (on your machine:
   `.venv/bin/python scripts/render_test_profiles.py`, then `tests/fixtures/profiles/rendered/DS1_resume.pdf`), or the
   test sample `frontend/e2e/.tmp/cv.pdf`. Go through onboarding: Read my documents → pick a role → Review → Confirm
   profile. The score appears (about 10–60 s).
3. **One demo email:** Recruiter → Data Scientist → pick an applicant → Start interview loop → approve the invite →
   Send. It arrives at `DEMO_EMAIL_TO` only (subject `[RightStaff demo → <candidate>] …`). Outbox shows it.
4. **IMAP:** reply to that email from your inbox, then Recruiter → Tasks → **Check inbox now** (the tooltip shows the
   last check). The reply appears in that candidate's workflow. Logs:
   `docker compose -f docker-compose.prod.yml logs api | grep "inbox:"`.
5. Spending today: `sqlite3 data/demo.db "select provider, model, requests from provider_usage where day = date('now','-7 hours')"`
   (the `*daily-cap*` rows are the paid-provider counts the caps use).

## 7. Update after a code change (server)

```bash
cd ~/rightstaff
git pull
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d                    # recreates what changed; data/ is kept
docker image prune -f                                              # old layers (disk)
```

Database migrations run by themselves when the API starts. To start the data over: stop the API
(`docker compose -f docker-compose.prod.yml stop api`), run the reseed command from step 5, then `up -d`.

Useful: `docker compose -f docker-compose.prod.yml logs -f api` · `… restart api` · `df -h` · `free -h`.

## 8. Day 30: tear down (your machine)

```bash
aws ec2 terminate-instances --instance-ids $ID
aws ec2 wait instance-terminated --instance-ids $ID
aws ec2 describe-volumes --filters Name=attachment.instance-id,Values=$ID      # should be empty (DeleteOnTermination)
#   if a volume is left: aws ec2 delete-volume --volume-id <vol-…>
aws ec2 describe-addresses                                                      # Elastic IPs, if you allocated one:
#   aws ec2 release-address --allocation-id <eipalloc-…>
aws ec2 delete-security-group --group-id $SG
aws ec2 delete-key-pair --key-name rightstaff && rm ~/.ssh/rightstaff.pem
```

Then revoke the server's keys at each provider (Jev, Gemini, Groq) and the mailbox app password.

---

## How this was checked locally

`docker-compose.prod.yml` ran on an Apple Silicon Mac (Docker via colima, native linux/arm64, the t4g architecture):

- **Fakes** (no model calls): `RS_ENV_FILE=deploy/verify-fake.env RS_DATA_DIR=./.verify/fake-data`, the demo seeded
  with `scripts/seed_recruiter_demo.py`, then the browser suite against the stack through the proxy:
  `E2E_BASE_URL=http://localhost E2E_API=http://localhost/api E2E_DB=$PWD/.verify/fake-data/demo.db npx playwright test`
  (run in `frontend/`).
- **Live smoke** with a copy of the local `.env`: reseed from the archive (0 calls), `/healthz`, one synthetic resume
  read and scored through the proxy.

`RS_ENV_FILE` and `RS_DATA_DIR` default to `.env` and `./data`; the server never sets them.

**Dependency versions** are pinned in `constraints-prod.txt` (the tested environment). After changing
`requirements.txt` or `requirements-prod.txt`: `.venv/bin/python scripts/freeze_constraints.py`, then rebuild.
