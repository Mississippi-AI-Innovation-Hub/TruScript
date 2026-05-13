# MSBN Transcript Verification System

AI-powered web application for the Mississippi Board of Nursing to automate nursing school transcript review. Built by **Team Bully Protocol** at Mississippi State University — AWS Innovation Hub, Spring 2026.

> **PoC Notice:** This is a functional prototype, not a production system.

---

## What It Does

Staff upload nursing school transcripts → AWS Textract + Rekognition analyze them → AI flags anomalies with risk scores (LOW / MEDIUM / HIGH / CRITICAL) → Staff CONFIRM or OVERRIDE each flag with an auditable justification.

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | React 18 + TypeScript + Tailwind CSS → S3 + CloudFront |
| Backend API | FastAPI (Python 3.11) → ECS Fargate |
| Auth | Keycloak 24 → ECS Fargate (JWT / realm roles) |
| Database | Amazon RDS PostgreSQL 14 |
| AI Pipeline | Amazon Textract + Rekognition + Lambda |
| Infrastructure | Terraform (us-east-1) |

---

## Live (Dev)

| | URL |
|---|---|
| Frontend | https://dbejm0k2ogvgf.cloudfront.net |
| API | https://msbn-dev-alb-2055778124.us-east-1.elb.amazonaws.com/api/v1/ |
| API Docs | `/api/v1/docs` |

---

## Quick Deploy

```bash
# 1. Infrastructure
cd infrastructure/environments/dev
terraform init && terraform apply -var-file="terraform.tfvars"

# 2. Backend
aws ecr get-login-password --region us-east-1 --profile msaih_IsbAdminsPS \
  | docker login --username AWS --password-stdin 526958953802.dkr.ecr.us-east-1.amazonaws.com

cd apps/backend
docker build --platform linux/amd64 -t msbn-backend:latest .
docker tag msbn-backend:latest 526958953802.dkr.ecr.us-east-1.amazonaws.com/msbn-backend:latest
docker push 526958953802.dkr.ecr.us-east-1.amazonaws.com/msbn-backend:latest
aws ecs update-service --cluster msbn-dev-cluster --service msbn-dev-backend \
  --force-new-deployment --region us-east-1 --profile msaih_IsbAdminsPS

# 3. Keycloak (after deploy, run once to set user passwords)
aws ecs update-service --cluster msbn-dev-cluster --service msbn-dev-keycloak \
  --force-new-deployment --region us-east-1 --profile msaih_IsbAdminsPS
# Once healthy:
cd apps/backend/keycloak && bash provision-users.sh

# 4. Frontend
bash scripts/deploy-frontend.sh dev
```

> **Note:** Always build Docker images with `--platform linux/amd64` — ECS Fargate is x86, dev machines are Apple Silicon.

---

## Project Structure

```
msbn/
├── apps/
│   ├── backend/          # FastAPI app, domain logic, DB migrations
│   │   └── keycloak/     # Realm config + provision scripts
│   └── frontend/         # React SPA
├── infrastructure/
│   ├── modules/          # Terraform modules (VPC, ECS, RDS, etc.)
│   └── environments/dev/ # Dev tfvars
├── scripts/
│   └── deploy-frontend.sh
└── MSBN_Project_Closeout.docx
```

---

## Known Limitations

- No HTTPS (HTTP only on ALB in dev)
- AI pipeline is partially implemented
- No automated tests
- Keycloak is single-instance (not HA)
- No CI/CD — deployments are manual


---

**Team Bully Protocol** · Mississippi State University · AWS Innovation Hub · Spring 2026
