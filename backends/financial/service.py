"""Manual SOAP helpers for financial transaction approvals."""

import random
from datetime import datetime
from xml.etree import ElementTree
from xml.sax.saxutils import escape


SOAP_ENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"
TNS = "marketplace.financial.v1"


class InvalidCardError(Exception):
    """Raised when payment data fails basic validation."""


def _parse_expiry(value: str) -> tuple[int, int] | None:
    text = value.strip()
    if "/" not in text:
        return None

    month_part, year_part = text.split("/", 1)
    if not (month_part.isdigit() and year_part.isdigit()):
        return None

    month = int(month_part)
    if month < 1 or month > 12:
        return None

    if len(year_part) == 2:
        year = 2000 + int(year_part)
    elif len(year_part) == 4:
        year = int(year_part)
    else:
        return None

    return month, year


def _is_expired(month: int, year: int) -> bool:
    now = datetime.utcnow()
    if year < now.year:
        return True
    if year == now.year and month < now.month:
        return True
    return False


def validate_request(
    *,
    user_name: str,
    credit_card_number: str,
    expiration_date: str,
    security_code: str,
) -> None:
    if not user_name or not user_name.strip():
        raise InvalidCardError("Missing required field: user_name")

    card_number = credit_card_number.strip()
    if not card_number or not card_number.isdigit() or not (12 <= len(card_number) <= 19):
        raise InvalidCardError("Invalid credit card number")

    cvv = security_code.strip()
    if not cvv or not cvv.isdigit() or len(cvv) not in (3, 4):
        raise InvalidCardError("Invalid security code")

    parsed_expiry = _parse_expiry(expiration_date)
    if parsed_expiry is None:
        raise InvalidCardError("Invalid expiration date format")

    month, year = parsed_expiry
    if _is_expired(month, year):
        raise InvalidCardError("Card is expired")


def process_transaction(
    *,
    user_name: str,
    credit_card_number: str,
    expiration_date: str,
    security_code: str,
) -> str:
    validate_request(
        user_name=user_name,
        credit_card_number=credit_card_number,
        expiration_date=expiration_date,
        security_code=security_code,
    )
    return "Yes" if random.random() < 0.9 else "No"


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def parse_process_transaction_request(xml_body: str) -> dict[str, str]:
    try:
        root = ElementTree.fromstring(xml_body.encode("utf-8"))
    except ElementTree.ParseError as exc:
        raise ValueError("Invalid XML body") from exc

    if _local_name(root.tag) != "Envelope":
        raise ValueError("Invalid SOAP envelope")

    body = None
    for child in root:
        if _local_name(child.tag) == "Body":
            body = child
            break
    if body is None:
        raise ValueError("Missing SOAP body")

    operation = None
    for child in body:
        if _local_name(child.tag) == "ProcessTransaction":
            operation = child
            break
    if operation is None:
        raise ValueError("Missing ProcessTransaction request")

    fields = {
        "user_name": "",
        "credit_card_number": "",
        "expiration_date": "",
        "security_code": "",
    }
    for child in operation:
        key = _local_name(child.tag)
        if key in fields:
            fields[key] = child.text or ""

    return fields


def soap_success_response(result: str) -> str:
    safe_result = escape(result)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:tns="marketplace.financial.v1">'
        "<soapenv:Body>"
        "<tns:ProcessTransactionResponse>"
        f"<tns:ProcessTransactionResult>{safe_result}</tns:ProcessTransactionResult>"
        "</tns:ProcessTransactionResponse>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )


def soap_fault_response(*, fault_code: str, fault_string: str) -> str:
    safe_code = escape(fault_code)
    safe_string = escape(fault_string)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soapenv:Body>"
        "<soapenv:Fault>"
        f"<faultcode>{safe_code}</faultcode>"
        f"<faultstring>{safe_string}</faultstring>"
        "</soapenv:Fault>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )


def wsdl_document(service_url: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<definitions
    xmlns="http://schemas.xmlsoap.org/wsdl/"
    xmlns:tns="{TNS}"
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
    targetNamespace="{TNS}">
  <types>
    <xsd:schema targetNamespace="{TNS}" elementFormDefault="qualified">
      <xsd:element name="ProcessTransaction">
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="user_name" type="xsd:string" />
            <xsd:element name="credit_card_number" type="xsd:string" />
            <xsd:element name="expiration_date" type="xsd:string" />
            <xsd:element name="security_code" type="xsd:string" />
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>
      <xsd:element name="ProcessTransactionResponse">
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="ProcessTransactionResult" type="xsd:string" />
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>
    </xsd:schema>
  </types>

  <message name="ProcessTransactionSoapIn">
    <part name="parameters" element="tns:ProcessTransaction" />
  </message>
  <message name="ProcessTransactionSoapOut">
    <part name="parameters" element="tns:ProcessTransactionResponse" />
  </message>

  <portType name="FinancialTransactionsPortType">
    <operation name="ProcessTransaction">
      <input message="tns:ProcessTransactionSoapIn" />
      <output message="tns:ProcessTransactionSoapOut" />
    </operation>
  </portType>

  <binding name="FinancialTransactionsBinding" type="tns:FinancialTransactionsPortType">
    <soap:binding style="document" transport="http://schemas.xmlsoap.org/soap/http" />
    <operation name="ProcessTransaction">
      <soap:operation soapAction="ProcessTransaction" style="document" />
      <input>
        <soap:body use="literal" />
      </input>
      <output>
        <soap:body use="literal" />
      </output>
    </operation>
  </binding>

  <service name="FinancialTransactionsService">
    <port name="FinancialTransactionsPort" binding="tns:FinancialTransactionsBinding">
      <soap:address location="{escape(service_url)}" />
    </port>
  </service>
</definitions>
"""
