from datetime import datetime
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, JSON,
    Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.sql import func

from app.db import Base


class ProcessedMessage(Base):
    """Loggar varje bearbetat Gmail-meddelande. message_id är unikt = dubblettskydd."""

    __tablename__ = "processed_messages"
    __table_args__ = (UniqueConstraint("message_id", name="uq_processed_message_id"),)

    id = Column(Integer, primary_key=True)
    message_id = Column(String(255), nullable=False, index=True)
    thread_id = Column(String(255), nullable=True)
    sender = Column(String(512), nullable=True)
    subject = Column(Text, nullable=True)
    received_at = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, server_default=func.now(), nullable=False)
    file_name = Column(String(512), nullable=True)
    drive_file_id = Column(String(255), nullable=True)
    drive_link = Column(Text, nullable=True)
    status = Column(String(64), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)

    vendor = Column(String(255), nullable=True)
    amount = Column(Float, nullable=True)
    currency = Column(String(16), nullable=True)
    receipt_date = Column(String(32), nullable=True)
    category = Column(String(64), nullable=True)
    summary = Column(Text, nullable=True)
    # FAS 5.9 — engelsk beskrivning för Bezala-utkast på formatet
    # "{vendor} + {place/purpose} + {date}", t.ex.
    # "Parking at Helsinki-Vantaa Airport P2, 22-24 April 2026".
    # summary (svenska) behålls för UI-visning; ai_description_en används
    # som description-fält när vi skickar transaktionen till Bezala.
    ai_description_en = Column(String(500), nullable=True)
    ai_confidence = Column(Integer, nullable=True)

    bezala_transaction_id = Column(String(255), nullable=True)
    bezala_upload_status = Column(String(32), nullable=True)
    bezala_error_message = Column(Text, nullable=True)
    # FAS 8.5 — tidsstämpel när ett kvitto kopplades till en Bezala-tx.
    # Sätts av match-to-bezala-endpointen och rensas av unmatch.
    # NULL för pre-existing rader och avkopplade rader.
    matched_at = Column(DateTime, nullable=True, index=True)

    # Snapshot av Bezala bill_line vid match-tillfället (för Matchade-vyn).
    # Sätts av match-to-bezala om bill_line kunde slås upp. Rensas av
    # unmatch. Tillåter UI att visa merchant/amount/date utan att fråga
    # Bezala API på nytt vid varje page-load.
    bezala_payment_merchant = Column(String(255), nullable=True)
    bezala_payment_amount = Column(Float, nullable=True)
    bezala_payment_currency = Column(String(16), nullable=True)
    bezala_payment_date = Column(String(32), nullable=True)

    # Soft-delete (FAS 5.1). deleted_at = NULL → aktiv rad.
    # delete_reason: manual | calendar | spam | misclassified
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    delete_reason = Column(String(32), nullable=True)

    # Länk-baserad PDF-hantering (ny): när leverantören skickar kvitto
    # bakom en klick-länk sparas URL:en här och status sätts till
    # 'needs_manual_download' tills användaren triggar /fetch-pdf.
    pending_link = Column(String(2048), nullable=True)

    # C33 / FAS 7a — manuell kvittouppladdning. 'company_card' (default)
    # går samma väg som Gmail-processade kvitton. 'private_card' är stubbad
    # i UI:t tills FAS 7b lägger till Netvisor-routing.
    payment_method = Column(String(20), nullable=True, default="company_card")
    # 'gmail' = vanligt Gmail-scan-flöde. 'manual_upload' = användaren
    # laddade upp filen via Travel Tinder upload-card.
    upload_source = Column(String(20), nullable=True, default="gmail")
    manual_uploaded_at = Column(DateTime, nullable=True)
    manual_comment = Column(Text, nullable=True)
    # C43 — dokumenttyp. NULL = vanligt kvitto. 'change_fee' = Finnair/
    # Amadeus-ombokning (prisskillnad/ändringsavgift) — räknas inte som
    # resans flygbiljett.
    kind = Column(String(32), nullable=True)
    # C44 — detaljer ur mailtexten (läses från Gmail, read-only):
    # Moovy/Finavia-parkering: infart/utfart (ISO, Helsingfors-tid).
    parking_entry_at = Column(String(40), nullable=True)
    parking_exit_at = Column(String(40), nullable=True)
    # Finnair/Amadeus: flygsegment [{flight_no, date, origin, dest, dep, arr}],
    # bokningsnummer (PNR) och e-biljettnummer.
    flight_segments = Column(JSON, nullable=True)
    booking_ref = Column(String(16), nullable=True)
    ticket_number = Column(String(32), nullable=True)
    # 'ok' | 'no_details' | 'error' — när detaljerna lästs (en gång per rad).
    details_status = Column(String(16), nullable=True)
    # C44 — dubblett av ett annat kvitto (message_id). Räknas inte i
    # checklistor eller kopplingsförslag. Bezala rörs aldrig.
    duplicate_of = Column(String(255), nullable=True)
    # C45 — vilket Gmail-konto mailet kom från: 'visma' (jobbkontot,
    # default) eller 'private' (Mikkos privata Gmail, bara allowlistade
    # kvittoavsändare som Apple).
    source_account = Column(String(16), nullable=True, default="visma")
    # C46 — 'forwarded' när bara Mikkos vidarebefordran fanns (Del 4)
    source_note = Column(String(32), nullable=True)
    # C47 — kvitto utan korttransaktion ännu. 'waiting_for_transaction'
    # (väntar), 'no_transaction' (ingen träff efter 14 dagar), 'private'
    # (Mikko: privat/annat kort), 'closed' (sluta vänta). NULL = inte i kön.
    waiting_status = Column(String(24), nullable=True)
    waiting_since = Column(DateTime, nullable=True)
    # C47 Del 6 — t.ex. 'date_corrected' (AI-datum utanför rimligt fönster).
    review_flag = Column(String(64), nullable=True)


class SavedFile(Base):
    """Unikhetsindex för filnamn + datum (tredje dubblettskiktet)."""

    __tablename__ = "saved_files"
    __table_args__ = (UniqueConstraint("file_name", "file_date", name="uq_filename_date"),)

    id = Column(Integer, primary_key=True)
    file_name = Column(String(512), nullable=False)
    file_date = Column(String(32), nullable=False)
    drive_file_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class ScanRun(Base):
    """Logg av varje schemalagd scanning."""

    __tablename__ = "scan_runs"

    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, server_default=func.now(), nullable=False)
    finished_at = Column(DateTime, nullable=True)
    messages_found = Column(Integer, default=0, nullable=False)
    messages_processed = Column(Integer, default=0, nullable=False)
    messages_skipped = Column(Integer, default=0, nullable=False)
    errors = Column(Integer, default=0, nullable=False)
    status = Column(String(32), default="running", nullable=False)
    notes = Column(Text, nullable=True)

    # Gate 1.5 — Loggtransparens. Array av filtrerade mail med reason +
    # confidence. NULL = gammal körning utan detaljer (tolkas som [] i API).
    filtered_messages = Column(JSON, nullable=True)
    # C46 — privat Gmail: kandidater och avvisade (okänd avsändare, bara antal)
    private_found = Column(Integer, nullable=True)
    private_rejected = Column(Integer, nullable=True)


class AppSettings(Base):
    """Applikationsinställningar (singleton — id=1)."""

    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)

    scan_interval_minutes = Column(Integer, nullable=False, default=60)
    ai_naming_enabled = Column(Boolean, nullable=False, default=True)
    auto_upload_enabled = Column(Boolean, nullable=False, default=False)
    confidence_threshold = Column(Integer, nullable=False, default=90)

    require_attachments = Column(Boolean, nullable=False, default=True)
    exclude_promotions = Column(Boolean, nullable=False, default=True)
    exclude_social = Column(Boolean, nullable=False, default=True)
    exclude_calendar = Column(Boolean, nullable=False, default=True)

    include_senders = Column(JSON, nullable=False, default=list)
    exclude_senders = Column(JSON, nullable=False, default=list)
    exclude_subjects = Column(JSON, nullable=False, default=list)

    # Auto-purge för papperskorg. 0 = aldrig (default). 30/60/90 dagar
    # är tillåtna värden i UI, men fältet lagrar vilken siffra som helst.
    trash_auto_purge_days = Column(Integer, nullable=False, default=0)

    # AI-tröskel: mail med confidence lägre än detta SPARAS INTE i DB
    # (raden hoppas permanent över — mark_done sätts i Gmail). Default 40.
    ai_min_confidence_to_save = Column(Integer, nullable=False, default=40)

    # Leverantörer där Bezala Bot ignorerar bilagor och letar efter en
    # kvitto-länk i mailets body istället. Förifylld med Arlanda Express.
    link_fetch_senders = Column(JSON, nullable=False, default=list)

    # Konvertera mail-body till PDF när bilaga saknas (t.ex. Moovy,
    # Skånetrafiken). Default ON.
    html_to_pdf_enabled = Column(Boolean, nullable=False, default=True)

    # Sätts av Gmail/Drive-klienterna när refresh-tokenen är ogiltig
    # (invalid_grant). UI visar varningsbanner + Återanslut-knapp.
    gmail_auth_required = Column(Boolean, nullable=False, default=False)
    drive_auth_required = Column(Boolean, nullable=False, default=False)
    # C45 — privat Gmail (bara gmail.readonly) behöver återanslutas.
    gmail_private_auth_required = Column(Boolean, nullable=False, default=False)

    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class OAuthToken(Base):
    """Persistenta OAuth-tokens (Gmail/Drive) — sparas i DB istället för
    fil eller env så de överlever Railway-redeploys.

    `service` är primärnyckel ('gmail' eller 'drive'). `token_data` är ett
    JSON-objekt med minst nyckeln `refresh_token` (kan även innehålla
    access_token/expiry för felsökning, men access_token genereras alltid
    om från refresh_token vid behov).
    """

    __tablename__ = "oauth_tokens"

    service = Column(String(32), primary_key=True)
    token_data = Column(JSON, nullable=False, default=dict)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CurrencyRate(Base):
    """Cache för historiska ECB-växelkurser från frankfurter.app.
    Historiska kurser ändras inte → ingen TTL, unik (date, from, to)."""

    __tablename__ = "currency_rates"
    __table_args__ = (
        UniqueConstraint("date", "from_currency", "to_currency",
                         name="uq_currency_rate_date_pair"),
    )

    id = Column(Integer, primary_key=True)
    date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    from_currency = Column(String(3), nullable=False)
    to_currency = Column(String(3), nullable=False)
    rate = Column(Float, nullable=False)
    fetched_at = Column(DateTime, server_default=func.now(), nullable=False)


class MaintenanceTask(Base):
    """Spår engångs-underhållsjobb som körts (kleaning, seed-data etc)."""

    __tablename__ = "maintenance_tasks"

    name = Column(String(128), primary_key=True)
    ran_at = Column(DateTime, server_default=func.now(), nullable=False)


class AiFeedback(Base):
    """FAS 8 — feedback-loop. Loggar 👍/👎 + rättelser från användaren
    så AI:n kan lära sig genom few-shot-exempel i nästa anrop.

    feedback_type: 'thumbs_up' | 'thumbs_down' | 'correction'
    field_name: 'vendor' | 'amount' | 'date' | 'category' (NULL för
    thumbs_up + thumbs_down utan specificerade fält).
    vendor_context: leverantörsnamn för indexering — gör det möjligt
    att hämta vendor-specifika few-shot-exempel.

    OBS: vi använder INTE en hård FK till processed_messages eftersom
    SQLite/Postgres-FK till en UNIQUE (icke-PK) kolumn beter sig olika.
    Föräldralösa rader (efter hard-delete) är OK — de behöver inte
    putsas bort, eftersom de fortfarande är användbara träningsdata."""

    __tablename__ = "ai_feedback"

    id = Column(Integer, primary_key=True)
    message_id = Column(String(255), nullable=False, index=True)
    feedback_type = Column(String(20), nullable=False)
    field_name = Column(String(50), nullable=True)
    ai_value = Column(Text, nullable=True)
    correct_value = Column(Text, nullable=True)
    vendor_context = Column(String(255), nullable=True, index=True)
    # FAS 8.1.1 — subject sparas tillsammans med sender för not_a_receipt-
    # rader så AI:n kan skilja på subject-mönster för samma avsändare
    # (t.ex. Finnair-bokningsbekräftelse vs Finnair-eticket-kvitto).
    subject_context = Column(String(500), nullable=True)
    created_at = Column(
        DateTime, server_default=func.now(), nullable=False, index=True,
    )


# ---------- FAS 11.1 — Resor (trip-gruppering) ----------


class Trip(Base):
    """En resa: en grupp av kvitton (flygbiljett som anchor + relaterade
    kostnader). Skapas av `suggest_trips` (status='suggested') och
    aktiveras när användaren accepterar.

    status:
      'suggested' = AI-förslag, väntar på användarbeslut
      'active'    = användaren har accepterat
      'rejected'  = användaren har avvisat (sparas för feedback)
      'archived'  = arkiverad / klar
    """

    __tablename__ = "trips"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    destination = Column(String(100), nullable=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    total_amount = Column(Numeric(10, 2), nullable=True)
    base_currency = Column(String(3), nullable=False, default="EUR")
    status = Column(String(20), nullable=False, default="suggested", index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    user_decision_at = Column(DateTime, nullable=True)
    ai_confidence = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)

    # Reserverade fält för framtida FAS 11.5 (Netvisor-integration). Inte
    # populerade i FAS 11.1 — bara strukturen finns på plats.
    netvisor_trip_id = Column(String(50), nullable=True)
    netvisor_synced_at = Column(DateTime, nullable=True)

    user_edited = Column(Boolean, nullable=False, default=False)

    # FAS 11.5.1 — per diem (traktamente)
    destination_country = Column(String(2), nullable=True)
    departure_home_at = Column(DateTime, nullable=True)
    return_home_at = Column(DateTime, nullable=True)
    trip_route = Column(Text, nullable=True)
    per_diem_calculation = Column(JSON, nullable=True)
    per_diem_amount = Column(Numeric(10, 2), nullable=True)
    per_diem_currency = Column(String(3), nullable=True)


Index("idx_trips_dates", Trip.start_date, Trip.end_date)


class TripMessage(Base):
    """Många-till-många mellan Trip och ProcessedMessage (via message_id).
    Soft-delete via removed_at — så vi behåller historik om användaren
    tar bort ett kvitto från resan.

    OBS: vi använder INTE en hård FK till processed_messages eftersom
    SQLite/Postgres-FK till en UNIQUE (icke-PK) kolumn är inkonsekvent
    (samma val som AiFeedback)."""

    __tablename__ = "trip_messages"
    __table_args__ = (
        UniqueConstraint("trip_id", "message_id", name="uq_trip_message"),
    )

    id = Column(Integer, primary_key=True)
    trip_id = Column(
        Integer,
        ForeignKey("trips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id = Column(String(255), nullable=False, index=True)
    added_by = Column(String(20), nullable=False)  # 'ai_suggestion' | 'manual'
    added_at = Column(DateTime, server_default=func.now(), nullable=False)
    removed_at = Column(DateTime, nullable=True)


class PerDiemRate(Base):
    """FAS 11.5.1 — Verohallinto-rates per land och år för traktamente.

    full_day_amount = kokopäiväraha (>10h)
    half_day_amount = osapäiväraha (>6h) eller halv ulkomaanpäiväraha
    source = 'verohallinto' eller 'manual'

    Seed:as vid första startup för 2026 (FI, SE, NO, LV)."""

    __tablename__ = "per_diem_rates"
    __table_args__ = (
        UniqueConstraint("year", "country_code", name="uq_per_diem_year_country"),
    )

    id = Column(Integer, primary_key=True)
    year = Column(Integer, nullable=False)
    country_code = Column(String(2), nullable=False)  # ISO 3166-1
    country_name = Column(String(100), nullable=False)
    full_day_amount = Column(Numeric(10, 2), nullable=False)
    half_day_amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="EUR")
    source = Column(String(50), nullable=True)
    source_url = Column(String(500), nullable=True)
    last_updated = Column(DateTime, server_default=func.now(), nullable=True)


Index("idx_per_diem_year_country", PerDiemRate.year, PerDiemRate.country_code)


class TripFeedback(Base):
    """Loggar användarens beslut (accept/reject/edit/wrong_grouping etc.)
    så Claude kan dra lärdom via few-shot. `details` är ett JSON-objekt
    med ändrade fält när feedback_type='edited' (t.ex. {title: {from, to}})."""

    __tablename__ = "trip_feedback"

    id = Column(Integer, primary_key=True)
    trip_id = Column(
        Integer,
        ForeignKey("trips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    feedback_type = Column(String(50), nullable=False)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class ExcludedVendor(Base):
    """FAS 11.1.1 — vendors som aldrig ska räknas som resekvitton.

    Lagrar substring-mönster (t.ex. 'anthropic', 'spotify') som
    matchas case-insensitive mot ProcessedMessage.vendor när
    trip_grouper bygger förslag.

    `added_by` är 'system' (default-listan, seedad vid migration) eller
    'user' (egna tillägg från Inställningar-vyn). Inga FK-relationer.
    """

    __tablename__ = "excluded_vendors"
    __table_args__ = (
        UniqueConstraint(
            "vendor_pattern", name="uq_excluded_vendors_pattern",
        ),
    )

    id = Column(Integer, primary_key=True)
    vendor_pattern = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    added_by = Column(String(20), nullable=False, default="user")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class BezalaVendorMapping(Base):
    """Bezala config-admin — vendor→account+VAT override.

    Använder substring-match (case-insensitive) mot ProcessedMessage.vendor.
    Default-mappningar (Moovy/Finavia → 67113 25.5%) seedas en gång per
    deploy via MaintenanceTask, men användaren får ändra/ta bort dem.

    OBS: själva applicerandet i upload-flödet implementeras i ett
    senare PR — den här PR:en lagrar bara konfigurationen.
    """

    __tablename__ = "bezala_vendor_mappings"
    __table_args__ = (
        UniqueConstraint(
            "vendor_pattern", name="uq_bezala_vendor_mappings_pattern",
        ),
    )

    id = Column(Integer, primary_key=True)
    vendor_pattern = Column(String(200), nullable=False)
    bezala_account_id = Column(Integer, nullable=False)
    vat_rate = Column(Numeric(5, 2), nullable=False)
    description_override = Column(String(500), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class HtmlOnlySender(Base):
    """HTML-only senders: avsändare som skickar kvittot i mail-bodyn
    istället för som PDF-bilaga (Skånetrafiken, Moovy notifieringar,
    Cursor, Airport LRS m.fl.).

    För dessa avsändare hoppar pipeline över has:attachment-filtret och
    låter html_to_pdf-konverteraren producera en PDF av mail-bodyn.

    `sender_pattern` är substring-match (case-insensitive) mot Gmail
    from-fältet. `is_active=False` deaktiverar utan att radera
    (möjliggör snabb on/off utan att tappa beskrivning + historik).
    """

    __tablename__ = "html_only_senders"
    __table_args__ = (
        UniqueConstraint(
            "sender_pattern", name="uq_html_only_senders_pattern",
        ),
    )

    id = Column(Integer, primary_key=True)
    sender_pattern = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


# ---------- Resor R1 — kalenderbaserade reseförslag ----------
#
# OBS: tabellnamnen har prefixet `calendar_` eftersom `trips` redan
# används av FAS 11.1 (kvittobaserade resor). R1 är read-only mot
# Netvisor/Bezala — dessa tabeller fylls bara från Google Calendar.


class CalendarTrip(Base):
    """En resa upptäckt i kalendern (flyg ut → flyg hem).

    status: 'suggested' (default) | 'edited' | 'approved'.
    Refresh ersätter bara 'suggested'-rader; edited/approved (R2) lämnas.
    """

    __tablename__ = "calendar_trips"

    id = Column(Integer, primary_key=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    start_at = Column(String(40), nullable=True)  # ISO med tidszon
    end_at = Column(String(40), nullable=True)
    destination = Column(Text, nullable=True)
    open_return = Column(Boolean, nullable=False, default=False)
    status = Column(String(20), nullable=False, default="suggested", index=True)
    source_event_ids = Column(JSON, nullable=False, default=list)
    flights = Column(JSON, nullable=False, default=list)
    hotels = Column(JSON, nullable=False, default=list)
    # C39 — 'missing_return' | 'needs_review'
    flags = Column(JSON, nullable=True, default=list)
    # C39 — alternativa utresor vid needs_review (t.ex. AY 951 + AY 953)
    flight_options = Column(JSON, nullable=True, default=list)
    # C39 — landskod(er) för destinationen, t.ex. "DK" eller "DK,NO"
    country = Column(String(20), nullable=True)
    # C40 — redigering via MCP (Claude-chatten)
    purpose = Column(Text, nullable=True)  # syfte, på engelska
    edited_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    # C41 — resetider (hemifrån → hem, med marginal på hemänden). start_at/
    # end_at ovan är flygtiderna (oförändrade). Netvisor använder resetiderna.
    travel_start_at = Column(String(40), nullable=True)
    travel_end_at = Column(String(40), nullable=True)
    # C44 — varifrån resetiderna kommer: 'parking' (Moovy infart/utfart ±
    # bilresa) | 'flight_default' (flygtider + marginal) | 'manual'.
    time_source = Column(String(20), nullable=True)
    # C44 — förslag för låsta resor (edited/approved) som aldrig skrivs in
    # automatiskt: {'travel_times': {...}, 'free_meals': {...},
    # 'flights': [...ombokade flyg...]}
    suggestions = Column(JSON, nullable=True)
    # C46 — Mikko avvisade resan ('rejected'). refresh_calendar återskapar
    # den aldrig (samma datum + samma flyg/händelser = samma resa).
    rejected_at = Column(DateTime, nullable=True)
    rejected_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


Index("idx_calendar_trips_dates", CalendarTrip.start_date, CalendarTrip.end_date)


class CalendarTripDay(Base):
    """En resdag i en CalendarTrip."""

    __tablename__ = "calendar_trip_days"
    __table_args__ = (
        UniqueConstraint("trip_id", "day_date", name="uq_calendar_trip_day"),
    )

    id = Column(Integer, primary_key=True)
    trip_id = Column(
        Integer,
        ForeignKey("calendar_trips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day_date = Column(Date, nullable=False)
    start_time = Column(String(5), nullable=True)  # HH:MM
    end_time = Column(String(5), nullable=True)
    destination = Column(Text, nullable=True)
    meetings = Column(JSON, nullable=False, default=list)
    hotel = Column(JSON, nullable=True)
    source_event_ids = Column(JSON, nullable=False, default=list)
    status = Column(String(20), nullable=False, default="suggested")
    # C40 — per dag: land + syfte (engelska)
    country = Column(String(20), nullable=True)
    purpose = Column(Text, nullable=True)
    # C41 — ankomstflygplats (IATA) skild från arbetsort (destination) och
    # varifrån orten kom: calendar_marker | meeting_title | meeting_room |
    # hotel | receipt | carried | airport | unconfirmed | manual
    airport = Column(String(8), nullable=True)
    destination_source = Column(String(32), nullable=True)
    # C44 — fria måltider (0–3) och källor: hotel_breakfast,
    # lunch_customer_day, dinner_kickoff, dinner_calendar, lunch_calendar,
    # ticket. Frukost loggas alltid i källorna men räknas bara utrikes när
    # TRIP_BREAKFAST_COUNTS_AS_MEAL_ABROAD=true.
    free_meals = Column(Integer, nullable=True)
    free_meal_sources = Column(JSON, nullable=True)
    # 'calendar' (förifyllt) | 'manual' (Mikko via update_trip_day)
    meals_source = Column(String(16), nullable=True)
    # C44 — 'manual' när syftet satts via update_trip_day.
    purpose_source = Column(String(16), nullable=True)
    # C46 — fri anteckning om måltiderna (engelska)
    free_meal_note = Column(Text, nullable=True)


# --- C40 — OAuth för MCP-connectorn (claude.ai) ----------------------------
#
# Bezala Bot är egen OAuth-auktoriseringsserver för /mcp. Alla hemligheter
# (koder, access- och refresh-tokens) lagras som SHA-256-hash — klartexten
# finns bara hos klienten.


class McpOAuthClient(Base):
    """Dynamiskt registrerad klient (RFC 7591). Publik klient (PKCE)."""

    __tablename__ = "mcp_oauth_clients"

    client_id = Column(String(64), primary_key=True)
    client_name = Column(String(255), nullable=True)
    redirect_uris = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class McpOAuthCode(Base):
    """Engångs-auktoriseringskod (giltig några minuter)."""

    __tablename__ = "mcp_oauth_codes"

    code_hash = Column(String(64), primary_key=True)
    client_id = Column(String(64), nullable=False)
    redirect_uri = Column(Text, nullable=False)
    code_challenge = Column(String(128), nullable=False)
    resource = Column(Text, nullable=False)
    scope = Column(String(255), nullable=True)
    subject = Column(String(255), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)


class McpOAuthPending(Base):
    """C41 — pågående authorize-begäran (från claude.ai till Tillåt/Avbryt).

    Lagras i databasen, inte i sessionskakan, så att den överlever
    inloggningen och ett dubbelklick på Tillåt. Id:t och csrf-värdet är
    slumpade; bara ägarkontot kan se consent-sidan där csrf visas.
    """

    __tablename__ = "mcp_oauth_pending"

    id = Column(String(64), primary_key=True)
    csrf = Column(String(64), nullable=False)
    client_id = Column(String(64), nullable=False)
    redirect_uri = Column(Text, nullable=False)
    state = Column(Text, nullable=True)
    code_challenge = Column(String(128), nullable=False)
    resource = Column(Text, nullable=False)
    scope = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    decision = Column(String(16), nullable=True)  # allow | deny


class McpOAuthToken(Base):
    """Access- eller refresh-token. kind: 'access' | 'refresh'."""

    __tablename__ = "mcp_oauth_tokens"

    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    kind = Column(String(16), nullable=False)
    client_id = Column(String(64), nullable=False, index=True)
    subject = Column(String(255), nullable=False)
    resource = Column(Text, nullable=False)
    scope = Column(String(255), nullable=True)
    # Access- och refresh-token från samma utfärdande delar grant_id så att
    # återkallelse/rotation kan slå ut hela paret.
    grant_id = Column(String(64), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


# --- C42 — fullständighetskontroll ------------------------------------------


class TripExpectation(Base):
    """En förväntad utläggspost för en kalenderresa (flyg, hotellnatt,
    parkering HEL, lokaltransport).

    status: 'found' | 'missing' | 'not_applicable' | 'optional'.
    set_by: 'auto' (räknas om varje gång) | 'user' (Mikko via MCP —
    skrivs aldrig över av omräkningen).
    slot: skiljer flera poster av samma slag åt, t.ex. hotellnattens datum
    ('2026-11-18') eller ankomstflygplatsen för lokaltransport ('CPH').
    """

    __tablename__ = "trip_expectations"
    __table_args__ = (
        UniqueConstraint("trip_id", "kind", "slot", name="uq_trip_expectation"),
    )

    id = Column(Integer, primary_key=True)
    trip_id = Column(
        Integer,
        ForeignKey("calendar_trips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(String(20), nullable=False)
    slot = Column(String(40), nullable=False, default="")
    status = Column(String(20), nullable=False, default="missing")
    set_by = Column(String(10), nullable=False, default="auto")
    message_id = Column(String(255), nullable=True)
    bill_line_id = Column(String(64), nullable=True)
    note = Column(Text, nullable=True)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class CardLine(Base):
    """C42 — Bezala-kortrad (bill_line) som saknar kvitto.

    Speglar Bezala GET /missing_receipts (endast läsning). En rad som
    försvinner ur Bezalas lista har fått kvitto → status 'resolved'.
    status: 'missing' | 'receipt_not_available' (Mikko/C43 — larmar inte)
    | 'private_expense' (C43 — privat köp, dras från lönen; larmar inte)
    | 'resolved'.
    """

    __tablename__ = "card_lines"
    __table_args__ = (UniqueConstraint("bill_line_id", name="uq_card_line_bill_line"),)

    id = Column(Integer, primary_key=True)
    bill_line_id = Column(String(64), nullable=False, index=True)
    txn_date = Column(Date, nullable=True, index=True)
    description = Column(Text, nullable=True)
    vendor = Column(String(255), nullable=True)
    amount = Column(Float, nullable=True)
    currency = Column(String(16), nullable=True)
    status = Column(String(32), nullable=False, default="missing")
    set_by = Column(String(10), nullable=True)
    note = Column(Text, nullable=True)
    # C43 — Bezala-utkast skapat för en private_expense-rad (på begäran).
    bezala_transaction_id = Column(String(255), nullable=True)
    first_seen_at = Column(DateTime, server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class PrivateGmailSender(Base):
    """C46 — avsändare som boten får läsa i privata Gmail (bara läsrätt).

    Tabellen är sanningen: PRIVATE_GMAIL_SENDER_ALLOWLIST lägger bara in
    saknade adresser vid start (added_via='env') och tar aldrig bort något.
    kind='email' = exakt adress, kind='domain' = '@domän.se' (även
    underdomäner). Ta bort = active=False (raden finns kvar)."""

    __tablename__ = "private_gmail_senders"
    __table_args__ = (
        UniqueConstraint("address", name="uq_private_gmail_senders_address"),
    )

    id = Column(Integer, primary_key=True)
    address = Column(String(255), nullable=False)
    kind = Column(String(8), nullable=False, default="email")
    active = Column(Boolean, nullable=False, default=True)
    added_at = Column(DateTime, server_default=func.now(), nullable=False)
    added_via = Column(String(8), nullable=False, default="ui")
    note = Column(Text, nullable=True)
    last_scan_at = Column(DateTime, nullable=True)
    backfill_status = Column(String(16), nullable=True)
    backfill_at = Column(DateTime, nullable=True)


class QueueDecision(Base):
    """C47 — beslut i svep-kön (Travel Tinder). item_id är kö-postens id
    (t.ex. 'receipt:2513379:msg-1'). decision = yes | no | later.
    'no' på ett kvitto/kortrad-par = föreslå inte paret igen. undo_data
    räcker för att ångra beslutet; undone_at sätts vid ångra."""

    __tablename__ = "queue_decisions"

    id = Column(Integer, primary_key=True)
    item_id = Column(String(255), nullable=False, index=True)
    kind = Column(String(32), nullable=False)
    decision = Column(String(8), nullable=False)
    source = Column(String(8), nullable=False, default="ui")
    undo_data = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    undone_at = Column(DateTime, nullable=True)


class Subscription(Base):
    """C47 Del 4 — prenumerationer på autopilot. Nya förslag är inactive
    tills Mikko bekräftar i svep-kön. account = Bezala-konto som Mikko valt
    tidigare för leverantören (aldrig gissat; NULL = flaggas)."""

    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    vendor_pattern = Column(String(200), nullable=False)
    sender = Column(String(255), nullable=True)
    expected_amount = Column(Float, nullable=True)
    currency = Column(String(3), nullable=True)
    cadence = Column(String(16), nullable=False, default="monthly")
    description_template = Column(String(200), nullable=True)
    account = Column(Integer, nullable=True)
    active = Column(Boolean, nullable=False, default=False)
    # seed | detected | ui | mcp
    origin = Column(String(16), nullable=False, default="detected")
    # suggested (väntar på Mikko) | confirmed | dismissed | private (C48:
    # Mikko: privat - kortrader markeras private_expense) | merged (C48)
    review_status = Column(String(16), nullable=False, default="suggested")
    # C48 — None (vanlig) | 'likely_private' (strömning/spel, se
    # SUBSCRIPTIONS_LIKELY_PRIVATE). Bara när leverantören har kortrader.
    kind = Column(String(24), nullable=True)
    # C48 — varför förslaget avfärdats automatiskt (t.ex. 'no_card_lines').
    note = Column(String(200), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class ExpectedRefund(Base):
    """C47 Del 5 — väntad återbetalning för en kortrad (t.ex. avbokad resa).
    status: open | matched | closed."""

    __tablename__ = "expected_refunds"

    id = Column(Integer, primary_key=True)
    bill_line_id = Column(String(64), nullable=False, index=True)
    vendor = Column(String(255), nullable=True)
    amount = Column(Float, nullable=True)
    currency = Column(String(16), nullable=True)
    txn_date = Column(Date, nullable=True)
    note = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="open")
    refund_bill_line_id = Column(String(64), nullable=True)
    matched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class CleanupLog(Base):
    """C48 Del 6 — vad datastädningen gjort (eller skulle göra i torrläge).

    run = körningens namn (t.ex. 'c47_backfill_v1'), rule = regel
    ('empty_error', 'pre_2026', 'date_corrected', 'finnair_duplicate',
    'hotel_duplicate', 'amount_missing', 'ignored_private_source', …).
    before/after = fältvärden före/efter. Läses av MCP get_cleanup_report.
    """

    __tablename__ = "cleanup_log"

    id = Column(Integer, primary_key=True)
    run = Column(String(64), nullable=False, index=True)
    rule = Column(String(40), nullable=False, index=True)
    message_id = Column(String(255), nullable=True, index=True)
    before = Column(JSON, nullable=True)
    after = Column(JSON, nullable=True)
    reason = Column(Text, nullable=True)
    dry_run = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class KvittoMailLog(Base):
    """C48 Del 5 — senaste mail till +kvitto-adressen (godkänt/avvisat)."""

    __tablename__ = "kvitto_mail_log"

    id = Column(Integer, primary_key=True)
    message_id = Column(String(255), nullable=False, index=True)
    sender = Column(String(512), nullable=True)
    received_at = Column(DateTime, nullable=True)
    attachments = Column(Integer, nullable=True)
    # accepted | rejected | no_attachment
    outcome = Column(String(20), nullable=False)
    reason = Column(String(200), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class BotFlag(Base):
    """C50 — beständiga flaggor (överlever omstart). Används som kill switch:
    `auto_couple_enabled=false` och `bezala_writes_halted=true` sätts av
    skyddsfunktionen om en Bezala-transaktion lämnar utkastläget."""

    __tablename__ = "bot_flags"

    key = Column(String(64), primary_key=True)
    value = Column(String(64), nullable=False)
    reason = Column(Text, nullable=True)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class BezalaWriteLog(Base):
    """C50 — varje skrivning boten gör mot Bezala (POST/PUT/DELETE), med
    status före/efter. Läses av list_bot_bezala_writes."""

    __tablename__ = "bezala_write_log"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(String(64), nullable=True, index=True)
    bill_line_id = Column(String(64), nullable=True)
    operation = Column(String(40), nullable=False)
    fields = Column(JSON, nullable=True)
    state_before = Column(JSON, nullable=True)
    state_after = Column(JSON, nullable=True)
    # ok | blocked | status_changed | error
    outcome = Column(String(20), nullable=False, default="ok")
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
