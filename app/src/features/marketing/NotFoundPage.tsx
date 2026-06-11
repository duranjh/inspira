import { Link } from "react-router-dom";

import { t } from "../../i18n";
import { MarketingLayout } from "./MarketingLayout";

export function NotFoundPage() {
  return (
    <MarketingLayout>
      <section className="marketing-section marketing-section--narrow">
        <h1 className="marketing-section__title">{t("not_found.title")}</h1>
        <p className="marketing-section__lede">{t("not_found.lede")}</p>
        <p style={{ marginTop: 24 }}>
          <Link to="/" className="marketing-inline-link">
            {t("not_found.home_link")}
          </Link>
        </p>
      </section>
    </MarketingLayout>
  );
}
