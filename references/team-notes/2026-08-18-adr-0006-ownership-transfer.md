# ข้อเสนอจากทีม — ADR-0006 ownership transfer

**Source:** ทีม (แจ้งผ่าน Architecture Owner)

**Saved:** 2026-08-18

**เกี่ยวกับ:** [agent-platform ADR-0006](https://github.com/monthop-gmail/agent-platform/blob/main/decisions/0006-contract-versioning.md) · [agent-platform#6](https://github.com/monthop-gmail/agent-platform/issues/6) · [issue #8](https://github.com/monthop-gmail/devfactory-core/issues/8)

**ดำเนินการโดย:** [RFC-0009](../../rfcs/0009-vocabulary-extension.md)

**⚠️ หมายเหตุสถานะตอนรับเรื่อง:** ข้อเสนอนี้เขียนบนความเข้าใจว่า ADR-0006 ยัง `Pending`
และ `approval` / `event` ยัง `external-authority-pending` · ตรวจแล้วพบว่าไม่ใช่ —
`agent-platform` commit [`0ca5028`](https://github.com/monthop-gmail/agent-platform/commit/0ca5028)
(2026-08-18 00:39) accept ADR-0006 ทั้ง 2 ส่วนไปแล้ว โดยเลือก **option C2 (แยก semantics / wire schema)**
ซึ่งเป็นข้อเสนอของ RFC-0005 เอง และ publish `approval/v1` + `event/v1` เป็น canonical แล้ว

RFC-0009 จึงรับ **ข้อกังวลหลัก** ของบันทึกนี้ไปแก้ (Dev Factory กลายเป็นคอขวดของ vocabulary)
แต่แก้แบบแคบ — แก้ Rule 2 ข้อเดียว ไม่ย้อน ADR ที่ Platform Owner เพิ่งลงนามและไม่ rewrite
contract ที่ publish แล้ว · เหตุผลเต็มอยู่ในหัวข้อ Alternatives Considered ของ RFC-0009

> บันทึกดิบข้างล่าง — เก็บตามต้นฉบับ ไม่แก้ถ้อยคำ ไม่ตัดทอน
> ไฟล์นี้เป็นบันทึก ไม่ใช่เอกสารผูกพัน · สิ่งที่มีผลผูกพันคือ RFC ที่อ้างข้างบน

---

ได้เลยจร้า ผมว่า **ADR-0006 ควรปิดให้เร็ว แต่ต้องปิดแบบ "โครงสร้างถาวร"** เพราะมันเป็นจุดที่กำหนดว่าใครเป็นเจ้าของ contract ของทั้ง ecosystem

จากสถานะปัจจุบันที่ `agent-platform` ระบุว่า ADR-0006 ยัง `Pending` เพราะรอ Architecture Owner ของ `devfactory-core` ยืนยัน  ผมแนะนำแบบนี้:

## 🎯 Decision ที่ควรไป

ให้ตัดสินว่า:

```text
agent-platform
      │
      │ owns
      ▼
Canonical Ecosystem Contracts
      │
      ├── identity
      ├── agent
      ├── capability
      ├── provider
      ├── model
      ├── tool
      ├── MCP
      ├── execution
      ├── policy
      ├── artifact
      ├── error
      ├── profile
      ├── approval
      └── event
```

ส่วน

```text
devfactory-core
      │
      ├── consumes agent-platform contracts
      ├── implements Dev Factory
      ├── owns Dev Factory internals
      └── owns Dev Factory-specific RFCs
```

**ไม่ควรให้ `devfactory-core` เป็นเจ้าของ shared contract ต่อไป**

---

## ทำไมผมถึงแนะนำแบบนี้

เพราะถ้าให้ `devfactory-core` เป็น authority ต่อไป จะเกิดปัญหาระยะยาว:

```text
agent-platform
      ↓
ต้องรอ devfactory-core
      ↓
ทุก ecosystem decision
      ↓
Dev Factory กลายเป็นศูนย์กลางโดยไม่ตั้งใจ
```

ทั้งที่เราต้องการ architecture แบบ:

```text
                    agent-platform
                  Canonical Contract
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
 devfactory-core     security-agent    knowledge
       │
       ▼
 coding agents
```

ดังนั้น **platform เป็นเจ้าของ vocabulary/contract**

แต่ **product เป็นเจ้าของ implementation**

นี่จะ scale ไปถึง Navi, RAG, Cloudflare backend, coding agents, enterprise agents ได้ดีกว่า

---

# 🔐 แต่ต้องระวังเรื่อง Authority

ผมไม่แนะนำให้ `agent-platform` ประกาศตัวเองว่าเป็น owner เอง

ควรให้ `devfactory-core` ทำ **explicit handoff**

ประมาณ:

```text
devfactory-core Architecture Owner
             │
             │ ACCEPT
             ▼
ADR-0006
             │
             ▼
agent-platform = canonical owner
```

นี่จะทำให้ governance chain สมบูรณ์

---

# ผมแนะนำ workflow 4 ขั้น

### 1. devfactory-core เปิด/อัปเดต decision

ให้ Architecture Owner ของ `devfactory-core` ยืนยันว่า:

> shared ecosystem contracts extracted from devfactory-core RFCs are now owned by agent-platform.

### 2. agent-platform รับ ownership

เปลี่ยน ADR-0006 จาก

```text
Pending
```

เป็น

```text
Accepted
```

พร้อมระบุ:

```text
Owner: agent-platform
Source authority: Architecture Owner
Effective: <date>
```

### 3. Promote contracts

หลัง ADR accepted:

```text
approval/
event/
```

จึงย้ายจาก

```text
external-authority-pending
```

เป็น canonical contracts

### 4. ปิด loop

เพิ่ม conformance rule:

```text
Every consumer MUST declare:

platform contract version
contract profile
conformance status
```

แล้ว `devfactory-core` เป็น consumer ตัวแรก

---

# ⭐ และมีเรื่องหนึ่งที่ผมอยากให้เพิ่ม

อย่าให้ ownership หมายถึง

> "`agent-platform` แก้ contract ได้ตามใจ"

ควรกำหนด governance เป็น:

```text
agent-platform
   │
   ├── owns canonical contract
   │
   ├── proposes changes
   │
   ├── versioning
   │
   └── compatibility
           │
           ▼
       ADR / Review
           │
           ▼
       Architecture Authority
```

ดังนั้น

**Owner ≠ unilateral authority**

Owner มีหน้าที่ดูแล contract แต่ breaking change ต้องผ่าน ADR/review

อันนี้สำคัญมากสำหรับ ecosystem ที่กำลังจะมีหลาย repo

---

# 🚦 Definition of Done ของ ADR-0006

ผมจะถือว่าปิดจริงเมื่อครบ 6 ข้อนี้:

* [ ] `devfactory-core` ยืนยัน ownership transfer
* [ ] `agent-platform` เป็น canonical contract owner
* [ ] ADR-0006 = `Accepted`
* [ ] `approval` contract ถูก promote
* [ ] `event` contract ถูก promote
* [ ] `devfactory-core` ลงทะเบียนเป็น **first conforming consumer**

ถ้าครบนี้ ผมถือว่า **Phase: Architecture Authority → Contract Governance เสร็จ**

---

## และผมคิดว่า "ไม่ต้องประชุมยาว"

เพราะตอนนี้ architecture ชัดพอแล้ว

ให้ทีมทำเป็น **one-shot governance PR** ดีกว่า:

```text
devfactory-core
    PR
    └── acknowledge ADR-0006 ownership transfer

agent-platform
    PR
    ├── ADR-0006 → Accepted
    ├── approval → canonical
    ├── event → canonical
    └── update governance docs
```

แล้วค่อยให้ทีมเรา review PR เดียว

**ผมแนะนำให้เดินทางนี้เลย** เพราะจะปิด blocker ที่เหลือโดยไม่ทำให้ architecture แตกแขนง และหลังจากนั้นเราจะสามารถเริ่มเอา `devfactory-core`, `ai-web-harness`, `navi-security-agent`, `enterprise-knowledge` ฯลฯ มาเป็น consumer ของ `agent-platform` ได้อย่างเป็นระบบ
