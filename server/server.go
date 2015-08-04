package server

import (
	"encoding/json"
	"fmt"
	"io/ioutil"
	"log"
	"net/http"
	"os"
	"strconv"

	"github.com/dooferlad/openid-go"
	"github.com/googollee/go-socket.io"
	"github.com/gorilla/mux"
	"github.com/gorilla/sessions"
	"gopkg.in/mgo.v2"
	"gopkg.in/mgo.v2/bson"
	"gopkg.in/yaml.v2"
)

type ServerState struct {
	bugs              *mgo.Collection
	meta              *mgo.Collection
	cards             *mgo.Collection
	lpUsers           *mgo.Collection
	messages          chan string
	socket            *socketio.Socket
	myUrl             string
	openIdCallbackUrl string
	settings          ServerSettings
	cookieStore       *sessions.CookieStore
}

type ServerSettings struct {
	LpTeam       string "lp_team"
	CookeSecret  []byte "cookieSecret"
	Hostname     string "hostname"
	PublicPort   int    "public_port"
	ExternalPort int    "external_port"
	PrivatePort  int    "private_port"
}

type LaunchpadTeam struct {
	Members []string
}

func (state ServerState) allowed(response http.ResponseWriter, request *http.Request) bool {
	// TODO: This clearly could be optimised:
	// teams could be stored in state and updated after some max age
	// teams could be transformed into a dictionary to make lookups quicker for large teams
	session, _ := state.cookieStore.Get(request, "simpleserve")
	if val, ok := session.Values["lpuser"]; ok {
		var teams []LaunchpadTeam
		query := state.lpUsers.Find(bson.M{"name": state.settings.LpTeam})
		count, err := query.Count()
		if err != nil {
			http.Error(response, err.Error(), 500)
			return false
		}
		if count != 1 {
			http.Error(response, "unexpected team match", 500)
			return false
		}
		err = query.All(&teams)
		if err != nil {
			http.Error(response, err.Error(), 500)
			return false
		}

		for _, person := range teams[0].Members {
			if person == val {
				return true
			}
		}
	}
	return false
}

func (state ServerState) requireLogin(response http.ResponseWriter, request *http.Request) bool {
	if state.allowed(response, request) {
		return true
	}
	http.Redirect(response, request, "/auth/login", 303)
	return false
}

// collectionToJson sends all data in a Mongo collection as JSON to a client
// by writing it directly into the HTTP response.
func collectionToJson(response http.ResponseWriter, collection *mgo.Collection) {
	// TODO: This could quite easily be cached and the cache invalidated when
	// the API gets a ping.
	var data []bson.M
	query := collection.Find(nil)
	count, err := query.Count()
	if err != nil {
		panic(err)
	}
	response.Header().Set("Content-Type", "application/vnd.api+json")
	if count > 0 {
		// We don't want to encode a zero length list as "null", so we only
		// write a response if there is something to write. This makes JSON
		// decoders happier.
		err = query.All(&data)
		if err != nil {
			panic(err)
		}
		json.NewEncoder(response).Encode(&data)
	}
}

func (state ServerState) apiBugsHandler(response http.ResponseWriter, request *http.Request) {
	if state.requireLogin(response, request) == false {
		return
	}
	collectionToJson(response, state.bugs)
}
func (state ServerState) apiMetaHandler(response http.ResponseWriter, request *http.Request) {
	if state.requireLogin(response, request) == false {
		return
	}
	collectionToJson(response, state.meta)
}
func (state ServerState) apiCardsHandler(response http.ResponseWriter, request *http.Request) {
	if state.requireLogin(response, request) == false {
		return
	}
	collectionToJson(response, state.cards)
}
func (state ServerState) apiPing(response http.ResponseWriter, request *http.Request) {
	state.messages <- "ping"
}

// forwardUpdatesToSocketIO sends a ping to the connected clients when we
// receive a ping from a data source that something updated.
func (state *ServerState) forwardUpdatesToSocketIO() {
	server, err := socketio.NewServer(nil)
	if err != nil {
		log.Fatal(err)
	}
	server.On("connection", func(so socketio.Socket) {
		so.Join("updates")
		state.socket = &so

		so.Emit("update", "hello")
		so.On("chat message", func(msg string) {
			log.Println("emit:", so.Emit("chat message", msg))
			so.BroadcastTo("chat", "chat message", msg)
		})
		so.On("disconnection", func() {
		})
	})
	server.On("error", func(so socketio.Socket, err error) {
		log.Println("error:", err)
	})

	http.Handle("/socket.io/", server)

	for {
		_ = <-state.messages
		if state.socket != nil {
			so := *state.socket
			so.Emit("update", "db")
		}
	}
}

// TODO: Make something that isn't just in-memory
var nonceStore = &openid.SimpleNonceStore{
	Store: make(map[string][]*openid.Nonce)}
var discoveryCache = &openid.SimpleDiscoveryCache{}

func (state *ServerState) loginHandler(w http.ResponseWriter, r *http.Request) {
	values := make(map[string]string)
	values["openid.ns.sreg"] = "http://openid.net/extensions/sreg/1.1"
	values["openid.sreg.required"] = "nickname"
	if url, err := openid.RedirectURL(
		"https://login.ubuntu.com/+openid",
		state.openIdCallbackUrl,
		"", values); err == nil {
		http.Redirect(w, r, url, 303)
	} else {
		log.Print(err)
	}
}

func (state *ServerState) loginCallbackHandler(w http.ResponseWriter, r *http.Request) {
	fullUrl := state.myUrl + r.URL.String()
	values, err := openid.Verify(fullUrl, discoveryCache, nonceStore)
	username := values.Get("openid.sreg.nickname")
	if err == nil {
		fmt.Printf("%s just logged in\n", username)
	} else {
		log.Print(err)
	}

	session, _ := state.cookieStore.Get(r, "simpleserve")
	session.Values["lpuser"] = username
	session.Save(r, w)
	http.Redirect(w, r, "/", 303)
}

// Run runs the web server
func Run() {
	// We spend most of our time presenting data from external sources, which
	// has already been inserted into a database (MongoDB), over our API as
	// JSON. Dial out to the database and get the collections set up.
	sess, err := mgo.Dial("localhost")
	if err != nil {
		panic(err)
	}
	defer sess.Close()
	state := ServerState{}
	db := sess.DB("juju_team_status")
	state.bugs = db.C("bugs_filtered")
	state.meta = db.C("projects_meta")
	state.cards = db.C("cards")
	state.lpUsers = db.C("lp_teams")

	settingsData, err := ioutil.ReadFile("settings.yaml")
	err = yaml.Unmarshal(settingsData, &state.settings)
	if err != nil {
		fmt.Printf("Error reading settings.yaml: %v\n", err)
		os.Exit(1)
	}

	state.cookieStore = sessions.NewCookieStore(state.settings.CookeSecret)

	// We tell the web client that we have updated information by communicating
	// with it over socket.io (http://socket.io/)
	state.socket = nil

	// We are told about database updates via web hook, which dumps the messages:= r.URL("")
	// in this queue for the socket.io handler function to pick up
	state.messages = make(chan string, 1000)

	// Set up our router
	// First the auth handlers
	var router = mux.NewRouter()
	var privateRouter = mux.NewRouter()

	auth := router.PathPrefix("/auth").Subrouter()
	auth.HandleFunc("/openidcallback", state.loginCallbackHandler)
	state.myUrl = "http://" + state.settings.Hostname + ":" + strconv.Itoa(state.settings.ExternalPort)
	state.openIdCallbackUrl = state.myUrl + "/auth/openidcallback"

	auth.HandleFunc("/login", state.loginHandler)

	// Serve API
	api := router.PathPrefix("/API").Subrouter()

	// It would be nice to have apiCollectionHandler and have a URL <--> collection mapping
	// in the settings file, then these could be set up in a for loop and we become more generic.
	//
	api.HandleFunc("/bugs", state.apiBugsHandler)
	api.HandleFunc("/meta", state.apiMetaHandler)
	//api.HandleFunc("/cards", state.apiCardsHandler)

	// Private API
	privateRouter.HandleFunc("/ping", state.apiPing)

	// Start the real time messaging handler
	go state.forwardUpdatesToSocketIO()

	// Serve static files
	router.PathPrefix("/").Handler(http.FileServer(http.Dir("./static/")))
	http.Handle("/", router)

	go http.ListenAndServe(":"+strconv.Itoa(state.settings.PrivatePort), privateRouter)
	log.Fatal(http.ListenAndServe(":"+strconv.Itoa(state.settings.PublicPort), nil))
}
