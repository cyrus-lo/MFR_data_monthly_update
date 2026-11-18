# Step 3 — Snowflake stage permission error

**Status:** Resolved  
**First documented:** 2026-08-18  
**Affected step:** Stage 3 — Upload to Snowflake

## Symptom

Step 3 failed while uploading an MFR workbook with:

```text
STEP 3 ERROR: Snowflake upload failed: 002003 (02000): SQL compilation error:
Stage 'CHUNWO_DB.PUBLIC.MFR_XLSX' does not exist or not authorized.
```

## Diagnosis

The Snowflake connection and login were successful. The failure occurred when Snowflake executed the `PUT` command and tried to resolve or write to the destination stage.

The configured stage was:

```text
@CHUNWO_DB.PUBLIC.MFR_XLSX
```

The error indicated that the active Snowflake role either did not have permission to use the stage or the stage name/database/schema was incorrect. In this incident, the upload succeeded after granting the role `READ` and `WRITE` privileges on the required stages.

## Resolution

Using the same role configured for the application, grant the required privileges on both Step 3 destination stages. Replace `<ROLE_NAME>` with the application role and adjust the stage names if necessary:

```sql
GRANT USAGE ON DATABASE CHUNWO_DB TO ROLE <ROLE_NAME>;
GRANT USAGE ON SCHEMA CHUNWO_DB.PUBLIC TO ROLE <ROLE_NAME>;

GRANT READ, WRITE ON STAGE CHUNWO_DB.PUBLIC.MFR_XLSX TO ROLE <ROLE_NAME>;
GRANT READ, WRITE ON STAGE CHUNWO_DB.PUBLIC.<SUB_STAGE_NAME> TO ROLE <ROLE_NAME>;
```

The exact grants must follow the organization's Snowflake security policy. The role must be able to resolve the database and schema and write files to each configured stage.

## Validation

Run these checks in Snowflake while using the same role as the application:

```sql
SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_DATABASE(), CURRENT_SCHEMA();

SHOW STAGES IN SCHEMA CHUNWO_DB.PUBLIC;

SHOW GRANTS ON STAGE CHUNWO_DB.PUBLIC.MFR_XLSX;
SHOW GRANTS ON STAGE CHUNWO_DB.PUBLIC.<SUB_STAGE_NAME>;
```

Confirm that:

- The stage names in the application configuration exist.
- The stage names include the `@` prefix in the `.env` file.
- `SF_EMFR_STAGE_NAME` and `SF_SUB_EMFR_STAGE_NAME` refer to different stages.
- The configured role is the role that received the grants.
- Both stages have the required `READ` and `WRITE` privileges.

After the grants were applied, Step 3 completed successfully and the MFR workbooks were uploaded.

## Configuration reference

Step 3 routes files by filename:

- `eMFR_*.xlsx` → `SF_EMFR_STAGE_NAME`
- `Sub_eMFR_*.xlsx` → `SF_SUB_EMFR_STAGE_NAME`

The stage settings are loaded from `mfr_snowflake_upload/.env`. Do not commit that file because it contains Snowflake credentials and environment-specific configuration.
