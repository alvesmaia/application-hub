# ZAP Scanning Report

ZAP by [Checkmarx](https://checkmarx.com/).


## Summary of Alerts

| Risk Level | Number of Alerts |
| --- | --- |
| High | 0 |
| Medium | 3 |
| Low | 2 |
| Informational | 7 |




## Insights

| Level | Reason | Site | Description | Statistic |
| --- | --- | --- | --- | --- |
| Low | Warning |  | ZAP warnings logged - see the zap.log file for details | 8    |
| Info | Informational | http://hub-web:8080 | Percentage of responses with status code 2xx | 95 % |
| Info | Informational | http://hub-web:8080 | Percentage of responses with status code 3xx | 3 % |
| Info | Informational | http://hub-web:8080 | Percentage of responses with status code 4xx | 1 % |
| Info | Informational | http://hub-web:8080 | Percentage of endpoints with content type application/json | 22 % |
| Info | Informational | http://hub-web:8080 | Percentage of endpoints with content type image/svg+xml | 1 % |
| Info | Informational | http://hub-web:8080 | Percentage of endpoints with content type text/css | 8 % |
| Info | Informational | http://hub-web:8080 | Percentage of endpoints with content type text/event-stream | 1 % |
| Info | Informational | http://hub-web:8080 | Percentage of endpoints with content type text/html | 48 % |
| Info | Informational | http://hub-web:8080 | Percentage of endpoints with content type text/javascript | 8 % |
| Info | Informational | http://hub-web:8080 | Percentage of endpoints with content type text/plain | 5 % |
| Info | Informational | http://hub-web:8080 | Percentage of endpoints with method GET | 81 % |
| Info | Informational | http://hub-web:8080 | Percentage of endpoints with method POST | 17 % |
| Info | Informational | http://hub-web:8080 | Percentage of endpoints with method PUT | 1 % |
| Info | Informational | http://hub-web:8080 | Count of total endpoints | 124    |
| Info | Informational | http://hub-web:8080 | Percentage of slow responses | 1 % |
| Info | Informational | https://fonts.googleapis.com | Percentage of endpoints with content type text/css | 100 % |
| Info | Informational | https://fonts.googleapis.com | Percentage of endpoints with method GET | 100 % |
| Info | Informational | https://fonts.googleapis.com | Count of total endpoints | 1    |
| Info | Informational | https://fonts.gstatic.com | Percentage of endpoints with content type font/woff2 | 100 % |
| Info | Informational | https://fonts.gstatic.com | Percentage of endpoints with method GET | 100 % |
| Info | Informational | https://fonts.gstatic.com | Count of total endpoints | 1    |







## Alerts

| Name | Risk Level | Number of Instances |
| --- | --- | --- |
| Absence of Anti-CSRF Tokens | Medium | Systemic |
| CSP: style-src unsafe-inline | Medium | Systemic |
| Sub Resource Integrity Attribute Missing | Medium | Systemic |
| Cross-Origin-Embedder-Policy Header Missing or Invalid | Low | Systemic |
| Private IP Disclosure | Low | 4 |
| Information Disclosure - Information in Browser localStorage | Informational | 4 |
| Information Disclosure - Information in Browser sessionStorage | Informational | 12 |
| Information Disclosure - Suspicious Comments | Informational | 3 |
| Non-Storable Content | Informational | Systemic |
| Storable and Cacheable Content | Informational | 2 |
| Storable but Non-Cacheable Content | Informational | 1 |
| User Controllable HTML Element Attribute (Potential XSS) | Informational | 2 |




## Alert Detail



### [ Absence of Anti-CSRF Tokens ](https://www.zaproxy.org/docs/alerts/10202/)



##### Medium (Low)

### Description

No Anti-CSRF tokens were found in a HTML submission form.
A cross-site request forgery is an attack that involves forcing a victim to send an HTTP request to a target destination without their knowledge or intent in order to perform an action as the victim. The underlying cause is application functionality using predictable URL/form actions in a repeatable way. The nature of the attack is that CSRF exploits the trust that a web site has for a user. By contrast, cross-site scripting (XSS) exploits the trust that a user has for a web site. Like XSS, CSRF attacks are not necessarily cross-site, but they can be. Cross-site request forgery is also known as CSRF, XSRF, one-click attack, session riding, confused deputy, and sea surf.

CSRF attacks are effective in a number of situations, including:
    * The victim has an active session on the target site.
    * The victim is authenticated via HTTP auth on the target site.
    * The victim is on the same local network as the target site.

CSRF has primarily been used to perform an action against a target site using the victim's privileges, but recent techniques have been discovered to disclose information by gaining access to the response. The risk of information disclosure is dramatically increased when the target site is vulnerable to XSS, because XSS can be used as a platform for CSRF, allowing the attack to operate within the bounds of the same-origin policy.

* URL: http://hub-web:8080/
  * Node Name: `http://hub-web:8080/`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `<form method="post" action="/logout">`
  * Other Info: `No known Anti-CSRF token [anticsrf, CSRFToken, __RequestVerificationToken, csrfmiddlewaretoken, authenticity_token, OWASP_CSRFTOKEN, anoncsrf, csrf_token, _csrf, _csrfSecret, __csrf_magic, CSRF, _token, _csrf_token, _csrfToken] was found in the following HTML form: [Form 1: "" ].`
* URL: http://hub-web:8080/account/password
  * Node Name: `http://hub-web:8080/account/password`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `<form method="post" action="/logout" class="auth-inline">`
  * Other Info: `No known Anti-CSRF token [anticsrf, CSRFToken, __RequestVerificationToken, csrfmiddlewaretoken, authenticity_token, OWASP_CSRFTOKEN, anoncsrf, csrf_token, _csrf, _csrfSecret, __csrf_magic, CSRF, _token, _csrf_token, _csrfToken] was found in the following HTML form: [Form 1: "" ].`
* URL: http://hub-web:8080/admin/env
  * Node Name: `http://hub-web:8080/admin/env`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `<form method="post" action="/logout">`
  * Other Info: `No known Anti-CSRF token [anticsrf, CSRFToken, __RequestVerificationToken, csrfmiddlewaretoken, authenticity_token, OWASP_CSRFTOKEN, anoncsrf, csrf_token, _csrf, _csrfSecret, __csrf_magic, CSRF, _token, _csrf_token, _csrfToken] was found in the following HTML form: [Form 1: "" ].`
* URL: http://hub-web:8080/admin/tasks
  * Node Name: `http://hub-web:8080/admin/tasks`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `<form method="post" action="/logout">`
  * Other Info: `No known Anti-CSRF token [anticsrf, CSRFToken, __RequestVerificationToken, csrfmiddlewaretoken, authenticity_token, OWASP_CSRFTOKEN, anoncsrf, csrf_token, _csrf, _csrfSecret, __csrf_magic, CSRF, _token, _csrf_token, _csrfToken] was found in the following HTML form: [Form 1: "" ].`
* URL: http://hub-web:8080/applications
  * Node Name: `http://hub-web:8080/applications`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `<form method="post" action="/logout">`
  * Other Info: `No known Anti-CSRF token [anticsrf, CSRFToken, __RequestVerificationToken, csrfmiddlewaretoken, authenticity_token, OWASP_CSRFTOKEN, anoncsrf, csrf_token, _csrf, _csrfSecret, __csrf_magic, CSRF, _token, _csrf_token, _csrfToken] was found in the following HTML form: [Form 1: "" ].`

Instances: Systemic


### Solution

Phase: Architecture and Design
Use a vetted library or framework that does not allow this weakness to occur or provides constructs that make this weakness easier to avoid.
For example, use anti-CSRF packages such as the OWASP CSRFGuard.

Phase: Implementation
Ensure that your application is free of cross-site scripting issues, because most CSRF defenses can be bypassed using attacker-controlled script.

Phase: Architecture and Design
Generate a unique nonce for each form, place the nonce into the form, and verify the nonce upon receipt of the form. Be sure that the nonce is not predictable (CWE-330).
Note that this can be bypassed using XSS.

Identify especially dangerous operations. When the user performs a dangerous operation, send a separate confirmation request to ensure that the user intended to perform that operation.
Note that this can be bypassed using XSS.

Use the ESAPI Session Management control.
This control includes a component for CSRF.

Do not use the GET method for any request that triggers a state change.

Phase: Implementation
Check the HTTP Referer header to see if the request originated from an expected page. This could break legitimate functionality, because users or proxies may have disabled sending the Referer for privacy reasons.

### Reference


* [ https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html ](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
* [ https://cwe.mitre.org/data/definitions/352.html ](https://cwe.mitre.org/data/definitions/352.html)


#### CWE Id: [ 352 ](https://cwe.mitre.org/data/definitions/352.html)


#### WASC Id: 9

#### Source ID: 3

### [ CSP: style-src unsafe-inline ](https://www.zaproxy.org/docs/alerts/10055/)



##### Medium (High)

### Description

Content Security Policy (CSP) is an added layer of security that helps to detect and mitigate certain types of attacks. Including (but not limited to) Cross Site Scripting (XSS), and data injection attacks. These attacks are used for everything from data theft to site defacement or distribution of malware. CSP provides a set of standard HTTP headers that allow website owners to declare approved sources of content that browsers should be allowed to load on that page — covered types are JavaScript, CSS, HTML frames, fonts, images and embeddable objects such as Java applets, ActiveX, audio and video files.

* URL: http://hub-web:8080/
  * Node Name: `http://hub-web:8080/`
  * Method: `GET`
  * Parameter: `content-security-policy`
  * Attack: ``
  * Evidence: `default-src 'self'; script-src http://hub-web:8080/assets/; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'`
  * Other Info: `style-src includes unsafe-inline.`
* URL: http://hub-web:8080/admin/audit
  * Node Name: `http://hub-web:8080/admin/audit`
  * Method: `GET`
  * Parameter: `content-security-policy`
  * Attack: ``
  * Evidence: `default-src 'self'; script-src http://hub-web:8080/assets/; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'`
  * Other Info: `style-src includes unsafe-inline.`
* URL: http://hub-web:8080/admin/env
  * Node Name: `http://hub-web:8080/admin/env`
  * Method: `GET`
  * Parameter: `content-security-policy`
  * Attack: ``
  * Evidence: `default-src 'self'; script-src http://hub-web:8080/assets/; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'`
  * Other Info: `style-src includes unsafe-inline.`
* URL: http://hub-web:8080/admin/tasks
  * Node Name: `http://hub-web:8080/admin/tasks`
  * Method: `GET`
  * Parameter: `content-security-policy`
  * Attack: ``
  * Evidence: `default-src 'self'; script-src http://hub-web:8080/assets/; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'`
  * Other Info: `style-src includes unsafe-inline.`
* URL: http://hub-web:8080/applications
  * Node Name: `http://hub-web:8080/applications`
  * Method: `GET`
  * Parameter: `content-security-policy`
  * Attack: ``
  * Evidence: `default-src 'self'; script-src http://hub-web:8080/assets/; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'`
  * Other Info: `style-src includes unsafe-inline.`

Instances: Systemic


### Solution

Ensure that your web server, application server, load balancer, etc. is properly configured to set the Content-Security-Policy header.

### Reference


* [ https://www.w3.org/TR/CSP/ ](https://www.w3.org/TR/CSP/)
* [ https://caniuse.com/#search=content+security+policy ](https://caniuse.com/#search=content+security+policy)
* [ https://content-security-policy.com/ ](https://content-security-policy.com/)
* [ https://github.com/HtmlUnit/htmlunit-csp ](https://github.com/HtmlUnit/htmlunit-csp)
* [ https://web.dev/articles/csp#resource-options ](https://web.dev/articles/csp#resource-options)


#### CWE Id: [ 693 ](https://cwe.mitre.org/data/definitions/693.html)


#### WASC Id: 15

#### Source ID: 3

### [ Sub Resource Integrity Attribute Missing ](https://www.zaproxy.org/docs/alerts/90003/)



##### Medium (High)

### Description

The integrity attribute is missing on a script or link tag served by an external server. The integrity tag prevents an attacker who have gained access to this server from injecting a malicious content.

* URL: http://hub-web:8080/
  * Node Name: `http://hub-web:8080/`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&amp;display=swap"/>`
  * Other Info: ``
* URL: http://hub-web:8080/admin
  * Node Name: `http://hub-web:8080/admin`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&amp;display=swap"/>`
  * Other Info: ``
* URL: http://hub-web:8080/admin/env
  * Node Name: `http://hub-web:8080/admin/env`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&amp;display=swap"/>`
  * Other Info: ``
* URL: http://hub-web:8080/admin/tasks
  * Node Name: `http://hub-web:8080/admin/tasks`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&amp;display=swap"/>`
  * Other Info: ``
* URL: http://hub-web:8080/applications
  * Node Name: `http://hub-web:8080/applications`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&amp;display=swap"/>`
  * Other Info: ``

Instances: Systemic


### Solution

Provide a valid integrity attribute to the tag.

### Reference


* [ https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Subresource_Integrity ](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Subresource_Integrity)


#### CWE Id: [ 345 ](https://cwe.mitre.org/data/definitions/345.html)


#### WASC Id: 15

#### Source ID: 3

### [ Cross-Origin-Embedder-Policy Header Missing or Invalid ](https://www.zaproxy.org/docs/alerts/90004/)



##### Low (Medium)

### Description

Cross-Origin-Embedder-Policy header is a response header that prevents a document from loading any cross-origin resources that don't explicitly grant the document permission (using CORP or CORS).

* URL: http://hub-web:8080/
  * Node Name: `http://hub-web:8080/`
  * Method: `GET`
  * Parameter: `Cross-Origin-Embedder-Policy`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: http://hub-web:8080/account/password
  * Node Name: `http://hub-web:8080/account/password`
  * Method: `GET`
  * Parameter: `Cross-Origin-Embedder-Policy`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: http://hub-web:8080/admin
  * Node Name: `http://hub-web:8080/admin`
  * Method: `GET`
  * Parameter: `Cross-Origin-Embedder-Policy`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: http://hub-web:8080/admin/env
  * Node Name: `http://hub-web:8080/admin/env`
  * Method: `GET`
  * Parameter: `Cross-Origin-Embedder-Policy`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: http://hub-web:8080/applications
  * Node Name: `http://hub-web:8080/applications`
  * Method: `GET`
  * Parameter: `Cross-Origin-Embedder-Policy`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``

Instances: Systemic


### Solution

Ensure that the application/web server sets the Cross-Origin-Embedder-Policy header appropriately, and that it sets the Cross-Origin-Embedder-Policy header to 'require-corp' for documents.
If possible, ensure that the end user uses a standards-compliant and modern web browser that supports the Cross-Origin-Embedder-Policy header (https://caniuse.com/mdn-http_headers_cross-origin-embedder-policy).

### Reference


* [ https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cross-Origin-Embedder-Policy ](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cross-Origin-Embedder-Policy)


#### CWE Id: [ 693 ](https://cwe.mitre.org/data/definitions/693.html)


#### WASC Id: 14

#### Source ID: 3

### [ Private IP Disclosure ](https://www.zaproxy.org/docs/alerts/2/)



##### Low (Medium)

### Description

A private IP (such as 10.x.x.x, 172.x.x.x, 192.168.x.x) or an Amazon EC2 private hostname (for example, ip-10-0-56-78) has been found in the HTTP response body. This information might be helpful for further attacks targeting internal systems.

* URL: http://hub-web:8080/admin/audit
  * Node Name: `http://hub-web:8080/admin/audit`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `10.231.0.1`
  * Other Info: `10.231.0.1
10.231.0.1
10.231.0.1
`
* URL: http://hub-web:8080/admin/audit%3Fscope=platform
  * Node Name: `http://hub-web:8080/admin/audit (scope)`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `10.231.0.1`
  * Other Info: `10.231.0.1
10.231.0.1
10.231.0.1
10.231.0.1
10.231.0.1
10.231.0.1
10.231.0.1
10.231.0.1
10.231.0.1
10.231.0.1
10.231.0.1
10.231.0.1
`
* URL: http://hub-web:8080/admin/logs
  * Node Name: `http://hub-web:8080/admin/logs`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `10.231.0.1`
  * Other Info: `10.231.0.1
10.231.0.1
10.231.0.1
`
* URL: http://hub-web:8080/api/admin/logs%3Fapp=all&minutes=60
  * Node Name: `http://hub-web:8080/api/admin/logs (app,minutes)`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `10.231.0.1`
  * Other Info: `10.231.0.1
10.231.0.1
10.231.0.1
`


Instances: 4

### Solution

Remove the private IP address from the HTTP response body. For comments, use JSP/ASP/PHP comment instead of HTML/JavaScript comment which can be seen by client browsers.

### Reference


* [ https://datatracker.ietf.org/doc/html/rfc1918 ](https://datatracker.ietf.org/doc/html/rfc1918)


#### CWE Id: [ 497 ](https://cwe.mitre.org/data/definitions/497.html)


#### WASC Id: 13

#### Source ID: 3

### [ Information Disclosure - Information in Browser localStorage ](https://www.zaproxy.org/docs/alerts/120000/)



##### Informational (High)

### Description

Information was stored in browser localStorage.
This is not unusual or necessarily unsafe - this informational alert has been raised to help you get a better understanding of what this app is doing. For more details see the Client tabs - this information was set directly in the browser and will therefore not necessarily appear in this form in any HTTP(S) messages.

* URL: http://hub-web:8080/ativos
  * Node Name: `http://hub-web:8080/ativos`
  * Method: `GET`
  * Parameter: `hub:assets:ativos`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub:assets:ativos=2.4.1
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/fornecedores
  * Node Name: `http://hub-web:8080/fornecedores`
  * Method: `GET`
  * Parameter: `hub:assets:fornecedores`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub:assets:fornecedores=1.8.0
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/servicos
  * Node Name: `http://hub-web:8080/servicos`
  * Method: `GET`
  * Parameter: `hub:assets:servicos`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub:assets:servicos=3.1.0
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/servicos/approvals
  * Node Name: `http://hub-web:8080/servicos/approvals`
  * Method: `GET`
  * Parameter: `hub:assets:servicos`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub:assets:servicos=3.1.0
Note that this alert will only be raised once for each URL + key.`


Instances: 4

### Solution

This is an informational alert and no action is necessary. 

### Reference



#### CWE Id: [ 359 ](https://cwe.mitre.org/data/definitions/359.html)


#### WASC Id: 13

#### Source ID: 3

### [ Information Disclosure - Information in Browser sessionStorage ](https://www.zaproxy.org/docs/alerts/120000/)



##### Informational (High)

### Description

Information was stored in browser sessionStorage.
This is not unusual or necessarily unsafe - this informational alert has been raised to help you get a better understanding of what this app is doing. For more details see the Client tabs - this information was set directly in the browser and will therefore not necessarily appear in this form in any HTTP(S) messages.

* URL: http://hub-web:8080/admin
  * Node Name: `http://hub-web:8080/admin`
  * Method: `GET`
  * Parameter: `hub-admin-toast`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub-admin-toast=Preferências salvas com sucesso.
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/admin/backups
  * Node Name: `http://hub-web:8080/admin/backups`
  * Method: `GET`
  * Parameter: `hub-admin-toast`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub-admin-toast=Teste de restore de Rede de Fornecedores concluído — bucket fornecedores (0 objetos) conferidos (sha256).
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/admin/deploys
  * Node Name: `http://hub-web:8080/admin/deploys`
  * Method: `GET`
  * Parameter: `hub-admin-toast`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub-admin-toast=Container hub-app-ativos__staging criado · imagem sha256:dde9cd67ea8c · health check ok · upstream http://hub-app-ativos__staging:8080
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/admin/env
  * Node Name: `http://hub-web:8080/admin/env`
  * Method: `GET`
  * Parameter: `hub-admin-toast`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub-admin-toast=Banco app_fornecedores provisionado; DATABASE_URL_FORNECEDORES vinculada ao app. Reinício pendente: use Aplicar (reiniciar).
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/admin/env%3Fapp=fornecedores
  * Node Name: `http://hub-web:8080/admin/env (app)`
  * Method: `GET`
  * Parameter: `hub-admin-toast`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub-admin-toast=Banco app_fornecedores já provisionado e acessível.
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/admin/health
  * Node Name: `http://hub-web:8080/admin/health`
  * Method: `GET`
  * Parameter: `hub-admin-toast`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub-admin-toast=Todas as aplicações estão disponíveis.
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/admin/runtime
  * Node Name: `http://hub-web:8080/admin/runtime`
  * Method: `GET`
  * Parameter: `hub-admin-toast`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub-admin-toast=Container hub-app-ativos reiniciado.
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/admin/runtime%3Fruntime=podman
  * Node Name: `http://hub-web:8080/admin/runtime (runtime)`
  * Method: `GET`
  * Parameter: `hub-admin-toast`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub-admin-toast=Container hub-app-ativos__staging reiniciado.
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/ativos
  * Node Name: `http://hub-web:8080/ativos`
  * Method: `GET`
  * Parameter: `hub:opened:ativos`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub:opened:ativos=1
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/fornecedores
  * Node Name: `http://hub-web:8080/fornecedores`
  * Method: `GET`
  * Parameter: `hub:opened:fornecedores`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub:opened:fornecedores=1
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/servicos
  * Node Name: `http://hub-web:8080/servicos`
  * Method: `GET`
  * Parameter: `hub:opened:servicos`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub:opened:servicos=1
Note that this alert will only be raised once for each URL + key.`
* URL: http://hub-web:8080/servicos/approvals
  * Node Name: `http://hub-web:8080/servicos/approvals`
  * Method: `GET`
  * Parameter: `hub:opened:servicos`
  * Attack: ``
  * Evidence: ``
  * Other Info: `The following data (key=value) was set: hub:opened:servicos=1
Note that this alert will only be raised once for each URL + key.`


Instances: 12

### Solution

This is an informational alert and no action is necessary. 

### Reference



#### CWE Id: [ 359 ](https://cwe.mitre.org/data/definitions/359.html)


#### WASC Id: 13

#### Source ID: 3

### [ Information Disclosure - Suspicious Comments ](https://www.zaproxy.org/docs/alerts/10027/)



##### Informational (Medium)

### Description

The response appears to contain suspicious comments which may help an attacker.

* URL: http://hub-web:8080/assets/admin.js
  * Node Name: `http://hub-web:8080/assets/admin.js`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `/* admin.js — interações da `
  * Other Info: `The following pattern was used: \bADMIN\b and was detected in likely comment: "/* admin.js — interações da Administração (e do submenu do rail) no shell do hub.
 *
 * As páginas /admin/* chegam prontas do ", see evidence field for the suspicious comment/snippet.`
* URL: http://hub-web:8080/assets/hub.js
  * Node Name: `http://hub-web:8080/assets/hub.js`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ` /{id}[/{section}][?query] chega do servidor `
  * Other Info: `The following pattern was used: \bQUERY\b and was detected 4 times, the first in likely comment: "/* hub.js — composição de fragmentos no shell do hub.
 *
 * A página /{id}[/{section}][?query] chega do servidor com o shell em ", see evidence field for the suspicious comment/snippet.`
* URL: http://hub-web:8080/assets/users.js
  * Node Name: `http://hub-web:8080/assets/users.js`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `mockup), chama /api/admin/users* e /api/admin`
  * Other Info: `The following pattern was used: \bADMIN\b and was detected 2 times, the first in likely comment: "/* users.js — Administração › Usuários e Tokens de API.
 *
 * As páginas chegam prontas do servidor; este script abre os formulá", see evidence field for the suspicious comment/snippet.`


Instances: 3

### Solution

Remove all comments that return information that may help an attacker and fix any underlying problems they refer to.

### Reference



#### CWE Id: [ 615 ](https://cwe.mitre.org/data/definitions/615.html)


#### WASC Id: 13

#### Source ID: 3

### [ Non-Storable Content ](https://www.zaproxy.org/docs/alerts/10049/)



##### Informational (Medium)

### Description

The response contents are not storable by caching components such as proxy servers. If the response does not contain sensitive, personal or user-specific information, it may benefit from being stored and cached, to improve performance.

* URL: http://hub-web:8080/
  * Node Name: `http://hub-web:8080/`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `no-store`
  * Other Info: ``
* URL: http://hub-web:8080/account/password
  * Node Name: `http://hub-web:8080/account/password`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `no-store`
  * Other Info: ``
* URL: http://hub-web:8080/admin
  * Node Name: `http://hub-web:8080/admin`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `no-store`
  * Other Info: ``
* URL: http://hub-web:8080/admin/env
  * Node Name: `http://hub-web:8080/admin/env`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `no-store`
  * Other Info: ``
* URL: http://hub-web:8080/applications
  * Node Name: `http://hub-web:8080/applications`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `no-store`
  * Other Info: ``

Instances: Systemic


### Solution

The content may be marked as storable by ensuring that the following conditions are satisfied:
The request method must be understood by the cache and defined as being cacheable ("GET", "HEAD", and "POST" are currently defined as cacheable)
The response status code must be understood by the cache (one of the 1XX, 2XX, 3XX, 4XX, or 5XX response classes are generally understood)
The "no-store" cache directive must not appear in the request or response header fields
For caching by "shared" caches such as "proxy" caches, the "private" response directive must not appear in the response
For caching by "shared" caches such as "proxy" caches, the "Authorization" header field must not appear in the request, unless the response explicitly allows it (using one of the "must-revalidate", "public", or "s-maxage" Cache-Control response directives)
In addition to the conditions above, at least one of the following conditions must also be satisfied by the response:
It must contain an "Expires" header field
It must contain a "max-age" response directive
For "shared" caches such as "proxy" caches, it must contain a "s-maxage" response directive
It must contain a "Cache Control Extension" that allows it to be cached
It must have a status code that is defined as cacheable by default (200, 203, 204, 206, 300, 301, 404, 405, 410, 414, 501).

### Reference


* [ https://datatracker.ietf.org/doc/html/rfc7234 ](https://datatracker.ietf.org/doc/html/rfc7234)
* [ https://datatracker.ietf.org/doc/html/rfc7231 ](https://datatracker.ietf.org/doc/html/rfc7231)
* [ https://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html ](https://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html)


#### CWE Id: [ 524 ](https://cwe.mitre.org/data/definitions/524.html)


#### WASC Id: 13

#### Source ID: 3

### [ Storable and Cacheable Content ](https://www.zaproxy.org/docs/alerts/10049/)



##### Informational (Medium)

### Description

The response contents are storable by caching components such as proxy servers, and may be retrieved directly from the cache, rather than from the origin server by the caching servers, in response to similar requests from other users. If the response data is sensitive, personal or user-specific, this may result in sensitive information being leaked. In some cases, this may even result in a user gaining complete control of the session of another user, depending on the configuration of the caching components in use in their environment. This is primarily an issue where "shared" caching servers such as "proxy" caches are configured on the local network. This configuration is typically found in corporate or educational environments, for instance.

* URL: http://hub-web:8080/robots.txt
  * Node Name: `http://hub-web:8080/robots.txt`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: `In the absence of an explicitly specified caching lifetime directive in the response, a liberal lifetime heuristic of 1 year was assumed. This is permitted by rfc7234.`
* URL: http://hub-web:8080/sitemap.xml
  * Node Name: `http://hub-web:8080/sitemap.xml`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: `In the absence of an explicitly specified caching lifetime directive in the response, a liberal lifetime heuristic of 1 year was assumed. This is permitted by rfc7234.`


Instances: 2

### Solution

Validate that the response does not contain sensitive, personal or user-specific information. If it does, consider the use of the following HTTP response headers, to limit, or prevent the content being stored and retrieved from the cache by another user:
Cache-Control: no-cache, no-store, must-revalidate, private
Pragma: no-cache
Expires: 0
This configuration directs both HTTP 1.0 and HTTP 1.1 compliant caching servers to not store the response, and to not retrieve the response (without validation) from the cache, in response to a similar request.

### Reference


* [ https://datatracker.ietf.org/doc/html/rfc7234 ](https://datatracker.ietf.org/doc/html/rfc7234)
* [ https://datatracker.ietf.org/doc/html/rfc7231 ](https://datatracker.ietf.org/doc/html/rfc7231)
* [ https://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html ](https://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html)


#### CWE Id: [ 524 ](https://cwe.mitre.org/data/definitions/524.html)


#### WASC Id: 13

#### Source ID: 3

### [ Storable but Non-Cacheable Content ](https://www.zaproxy.org/docs/alerts/10049/)



##### Informational (Medium)

### Description

The response contents are storable by caching components such as proxy servers, but will not be retrieved directly from the cache, without validating the request upstream, in response to similar requests from other users.

* URL: http://hub-web:8080/assets/logo.svg
  * Node Name: `http://hub-web:8080/assets/logo.svg`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `no-cache`
  * Other Info: ``


Instances: 1

### Solution



### Reference


* [ https://datatracker.ietf.org/doc/html/rfc7234 ](https://datatracker.ietf.org/doc/html/rfc7234)
* [ https://datatracker.ietf.org/doc/html/rfc7231 ](https://datatracker.ietf.org/doc/html/rfc7231)
* [ https://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html ](https://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html)


#### CWE Id: [ 524 ](https://cwe.mitre.org/data/definitions/524.html)


#### WASC Id: 13

#### Source ID: 3

### [ User Controllable HTML Element Attribute (Potential XSS) ](https://www.zaproxy.org/docs/alerts/10031/)



##### Informational (Low)

### Description

This check looks at user-supplied input in query string parameters and POST data to identify where certain HTML attribute values might be controlled. This provides hot-spot detection for XSS (cross-site scripting) that will require further review by a security analyst to determine exploitability.

* URL: http://hub-web:8080/admin/audit%3Fscope=platform
  * Node Name: `http://hub-web:8080/admin/audit (scope)`
  * Method: `GET`
  * Parameter: `scope`
  * Attack: ``
  * Evidence: ``
  * Other Info: `User-controlled HTML attribute values were found. Try injecting special characters to see if XSS might be possible. The page at the following URL:

http://hub-web:8080/admin/audit?scope=platform

appears to include user input in:
a(n) [header] tag [class] attribute

The user input found was:
scope=platform

The user-controlled value was:
platform-header`
* URL: http://hub-web:8080/admin/env%3Fapp=ativos
  * Node Name: `http://hub-web:8080/admin/env (app)`
  * Method: `GET`
  * Parameter: `app`
  * Attack: ``
  * Evidence: ``
  * Other Info: `User-controlled HTML attribute values were found. Try injecting special characters to see if XSS might be possible. The page at the following URL:

http://hub-web:8080/admin/env?app=ativos

appears to include user input in:
a(n) [article] tag [data-env-app] attribute

The user input found was:
app=ativos

The user-controlled value was:
ativos`


Instances: 2

### Solution

Validate all input and sanitize output it before writing to any HTML attributes.

### Reference


* [ https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html ](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html)


#### CWE Id: [ 20 ](https://cwe.mitre.org/data/definitions/20.html)


#### WASC Id: 20

#### Source ID: 3


