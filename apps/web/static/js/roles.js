export const ADMIN_ROLES = ["admin", "publisher"];

let adminRoles = ADMIN_ROLES;

export function setAdminRoles(roles) {
  if (Array.isArray(roles) && roles.length) {
    adminRoles = roles.map((role) => String(role));
  }
}

export function canAccessAdmin(role) {
  return adminRoles.includes(role || "");
}

export function canAccessClient(role) {
  return Boolean(role);
}
