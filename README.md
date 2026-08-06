# 🚀 Platform Engineering CLI - AWS Self-Service Provisioning

A robust Python-based Self-Service CLI tool designed for developers to provision and manage AWS resources (`EC2`, `S3`, `Route53`) within strict operational guardrails and security standards.

Built using **Python 3**, **Boto3**, and **Click**.

---

## 📋 Table of Contents
- [Overview & Architecture](#-overview--architecture)
- [Key Features & Guardrails](#-key-features--guardrails)
- [Prerequisites](#-prerequisites)
- [Installation](#-installation)
- [Tagging Specification](#-tagging-specification)
- [Usage Examples](#-usage-examples)
  - [EC2 Management](#1-ec2-instances)
  - [S3 Management](#2-s3-buckets)
  - [Route53 Management](#3-route53-dns)
- [Security Controls](#-security-controls)
- [Demo Evidence](#-demo-evidence)
- [Cleanup Guide](#-cleanup-guide)

---

## 🏗️ Overview & Architecture

The Platform CLI serves as an abstraction layer over AWS services, enabling developers to request development infrastructure independently while guaranteeing:
* **Scope Isolation**: Operations (`start`, `stop`, `upload`, `record updates`) are strictly limited to resources created by this tool.
* **Resource Optimization**: Cost-saving limits on instance counts and allowed types.
* **Compliance**: Standardized metadata tagging across all provisioned resources.

---

## 🛡️ Key Features & Guardrails

| Service | Feature | Enforced Constraint / Guardrail |
| :--- | :--- | :--- |
| **EC2** | Instance Creation | Allowed types strictly restricted to `t3.micro` or `t2.small`. |
| **EC2** | Capacity Hard Cap | Maximum **2** running/pending CLI-managed instances per account. |
| **EC2** | AMI Resolution | Auto-fetches latest stable **Ubuntu 22.04** or **Amazon Linux 2023** via SSM Parameter Store. |
| **EC2** | Management | `start`/`stop` restricted exclusively to `platform-cli` tagged instances. |
| **S3** | Bucket Creation | Defaults to private with Block Public Access enabled; public creation requires explicit confirmation. |
| **S3** | File Upload | Uploads allowed ONLY to CLI-tagged buckets. |
| **Route53**| DNS Zones & Records | Record creation/deletion strictly scoped to CLI-owned Hosted Zones. |

---

## 🔧 Prerequisites

Before running the CLI, ensure you have:
1. **Python 3.9+** installed.
2. **AWS CLI** installed and configured (`aws configure`).
3. Valid IAM credentials / Role with least-privilege permissions for EC2, S3, Route53, and SSM.

---


   
