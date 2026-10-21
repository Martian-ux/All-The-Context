-- Local access metadata, not portable memory authority. Legacy clients remain
-- explicitly unbound; a required binding can never silently downgrade to legacy.
ALTER TABLE client_registrations ADD COLUMN project_binding_required INTEGER
    NOT NULL DEFAULT 0 CHECK (project_binding_required IN (0,1));

CREATE TABLE canonical_projects (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    anchor_record_id TEXT NOT NULL,
    project_scope TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    UNIQUE(vault_id, anchor_record_id),
    UNIQUE(vault_id, project_scope)
);
CREATE TABLE principal_project_bindings (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    client_id TEXT NOT NULL UNIQUE REFERENCES client_registrations(id),
    canonical_project_id TEXT NOT NULL REFERENCES canonical_projects(id),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version=1),
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TRIGGER canonical_project_identity_immutable
BEFORE UPDATE ON canonical_projects
WHEN NEW.id<>OLD.id OR NEW.vault_id<>OLD.vault_id
 OR NEW.anchor_record_id<>OLD.anchor_record_id OR NEW.project_scope<>OLD.project_scope
 OR NEW.created_at<>OLD.created_at
 OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NOT OLD.revoked_at)
BEGIN SELECT RAISE(ABORT, 'canonical project identity is immutable'); END;
CREATE TRIGGER principal_project_binding_immutable
BEFORE UPDATE ON principal_project_bindings
WHEN NEW.id<>OLD.id OR NEW.vault_id<>OLD.vault_id OR NEW.client_id<>OLD.client_id
 OR NEW.canonical_project_id<>OLD.canonical_project_id
 OR NEW.schema_version<>OLD.schema_version OR NEW.created_at<>OLD.created_at
 OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NOT OLD.revoked_at)
BEGIN SELECT RAISE(ABORT, 'project binding is immutable'); END;
CREATE TRIGGER project_binding_cannot_downgrade
BEFORE UPDATE OF project_binding_required ON client_registrations
WHEN OLD.project_binding_required=1 AND NEW.project_binding_required<>1
BEGIN SELECT RAISE(ABORT, 'required project binding cannot downgrade'); END;

UPDATE vaults SET schema_version=21 WHERE schema_version<21;
