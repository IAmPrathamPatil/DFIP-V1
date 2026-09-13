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

export function inspectorClients(session) {
  const rows = session && Array.isArray(session.clients) ? session.clients : [];
  return rows.filter((item) => item && (item.role === "publisher" || item.role === "admin"));
}

export function isCompanyInactive(item) {
  return Boolean(item && item.lifecycle_status === "inactive");
}

export function operationalClients(session) {
  return inspectorClients(session).filter((item) => !isCompanyInactive(item));
}

export function needsCompanySelection(session) {
  if (!session || !canAccessAdmin(session.role)) return false;
  const operational = operationalClients(session);
  if (!session.client_id && operational.length > 1) return true;
  if (!session.client_id) return false;
  const current = inspectorClients(session).find((item) => item.client_id === session.client_id);
  return isCompanyInactive(current) && operational.length > 0;
}

export function allowsInactiveCompanyRoute(routeName) {
  return routeName === "admin-companies" || routeName === "admin-publications";
}

export function companyLabel(item) {
  if (!item) return "";
  return item.name || item.code || item.client_id;
}
