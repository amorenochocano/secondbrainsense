--- =============================================
-- sp_ejemplo.sql — fixture para tests del pipeline Brain F1.
-- Procedimiento SQL real del proyecto Second Brain.
-- Contiene CTEs, comentarios de documentación y lógica SQL real.
--- =============================================

--- =============================================
-- DOCUMENTACIÓN
-- Procedimiento: sp_load_sm_ac_tables
-- Descripción: Carga optimizada de tablas del modelo semántico
-- Parámetros: ninguno
-- Uso: EXEC [main].[sp_load_sm_tables]
-- Dependencias: admin_project_users, admin_users, admin_roles
--- =============================================
CREATE OR ALTER PROCEDURE [main].[sp_load_sm_tables]
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @columns       NVARCHAR(MAX);
    DECLARE @selectColumns NVARCHAR(MAX);
    DECLARE @sql           NVARCHAR(MAX);

    BEGIN TRY

        --- =============================================
        -- TABLA 1: sm_admin_project_users
        -- Columnas calculadas:
        --   project_id+user_id : bim360_project_id + '_' + user_id
        --   user_autodesk_id   : lookup desde admin_users.autodesk_id
        --   project_roles      : STRING_AGG de roles con OUTER APPLY
        --   product_services_access : productos ACC o servicios BIM360
        --- =============================================
        BEGIN TRY
            TRUNCATE TABLE [main].[sm_admin_project_users];

            -- Obtener columnas originales excluyendo calculadas y timestamps
            SELECT @columns = STRING_AGG('apu.' + QUOTENAME(COLUMN_NAME), ',')
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'main'
              AND TABLE_NAME   = 'admin_project_users'
              AND COLUMN_NAME NOT IN (
                  'fec_carga_rch', 'fec_carga_std',
                  'project_id+user_id', 'user_autodesk_id',
                  'project_roles', 'product_services_access'
              );

            SET @sql =
                'INSERT INTO [main].[sm_admin_project_users]
                 (' + @columns + ',
                  [project_id+user_id], [user_autodesk_id],
                  [project_roles], [product_services_access],
                  fec_carga_rch, fec_carga_std)
                 SELECT ' + @columns + ',
                        -- Columna concatenada project_id+user_id
                        CAST(apu.bim360_project_id AS VARCHAR(50))
                            + ''_'' + CAST(apu.user_id AS VARCHAR(50)),
                        -- user_autodesk_id: lookup por JOIN
                        au.autodesk_id,
                        -- project_roles: pre-agregado con OUTER APPLY
                        roles.project_roles,
                        -- product_services_access: COALESCE productos/servicios
                        COALESCE(products.product_access, services.service_access),
                        GETDATE(), GETDATE()
                 FROM [main].[admin_project_users] apu
                 LEFT JOIN [main].[admin_users] au
                   ON apu.user_id = au.id
                 OUTER APPLY (
                     SELECT STRING_AGG(r.name, '','') AS project_roles
                     FROM   [main].[admin_project_user_roles] pur
                     JOIN   [main].[admin_roles] r ON pur.role_id = r.id
                     WHERE  pur.project_id = apu.bim360_project_id
                       AND  pur.user_id    = apu.user_id
                 ) roles
                 OUTER APPLY (
                     SELECT STRING_AGG(p.name, '','') AS product_access
                     FROM   [main].[admin_products] p
                     WHERE  apu.acc_project = ''t''
                 ) products
                 OUTER APPLY (
                     SELECT STRING_AGG(s.name, '','') AS service_access
                     FROM   [main].[admin_services] s
                     WHERE  apu.acc_project <> ''t''
                 ) services';

            EXEC sp_executesql @sql;

            PRINT 'OK: sm_admin_project_users cargada correctamente';
        END TRY
        BEGIN CATCH
            PRINT 'ERROR en sm_admin_project_users: ' + ERROR_MESSAGE();
            THROW;
        END CATCH;

    END TRY
    BEGIN CATCH
        PRINT 'ERROR general en sp_load_sm_tables: ' + ERROR_MESSAGE();
        THROW;
    END CATCH;
END;
GO
